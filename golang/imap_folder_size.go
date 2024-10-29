package main

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/term"

	"github.com/BrianLeishman/go-imap"
	mapset "github.com/deckarep/golang-set/v2"
	flag "github.com/spf13/pflag"
	"github.com/spf13/viper"
)

type folder_name_and_flags struct {
	name  string
	flags []string
}

type message_infos struct {
	id     int
	size   int
	date   time.Time
	flags  []string
	folder string
}

type folder_stats struct {
	name     string
	messages int
	unreads  int
	size     int
	quota    float32
	contents []message_infos
}

var imap_server string
var imap_details bool
var no_trace bool

var special_folder_flags = mapset.NewSet[string]()
var known_folder_flags = mapset.NewSet[string]()

var imap_folder_list_re *regexp.Regexp
var imap_quota_re *regexp.Regexp
var imap_folder_examine_re *regexp.Regexp
var imap_folder_search_re *regexp.Regexp
var imap_folder_fetch_re *regexp.Regexp
var imap_message_attributes = make(map[string]*regexp.Regexp, 4)

func initialize_env_and_cmd_line_config() {
	// Environment parsing
	viper.AutomaticEnv() // read value ENV variable
	// Set default value
	viper.SetEnvPrefix("imap")
	viper.SetDefault("server", "imap.gmail.com")
	viper.SetDefault("details", false)
	viper.SetDefault("no_trace", false)
	viper.SetDefault("user", "")
	viper.SetDefault("password", "")
	viper.SetDefault("debug", false)

	// Command line arguments parsing
	flag.String("server", "imap.gmail.com", "the IMAP server DNS name or IP address")
	flag.Bool("details", false, "get IMAP messages details (default to false)")
	flag.Bool("no_trace", false, "disable deep tracing (default to false)")
	flag.String("user", "", "the IMAP user name/login")
	flag.String("password", "", "the IMAP user password")
	flag.Bool("debug", false, "enable IMAP debugging (default to false)")
	flag.Parse()
	viper.BindPFlags(flag.CommandLine)
}

func initialize_globals() {
	// Initialize IMAP folders flags
	special_folder_flags.Add("\\Noselect")
	special_folder_flags.Add("\\All")
	special_folder_flags.Add("\\Important")

	known_folder_flags.Add("\\HasNoChildren")
	known_folder_flags.Add("\\HasChildren")
	known_folder_flags.Add("\\Drafts")
	known_folder_flags.Add("\\Sent")
	known_folder_flags.Add("\\Junk")
	known_folder_flags.Add("\\Trash")
	known_folder_flags.Add("\\Flagged")
	known_folder_flags = known_folder_flags.Union(special_folder_flags)

	// Regular expressions
	imap_folder_list_re = regexp.MustCompile("^* LIST \\(([^\\)]*)\\) \\\"([^\\s]+)\\\" \\\"(.*)\\\"$")
	imap_quota_re = regexp.MustCompile("^* QUOTA [^\\(]+ \\(STORAGE (\\d+) (\\d+)\\)$")
	// Could factorize the following with RECENT but seems like it is very
	// rarely supported/meaningful
	// Potential addition, parse the FLAGS line from EXAMINE response to gather
	// all potential messages flags
	imap_folder_examine_re = regexp.MustCompile("^* (\\d+) EXISTS$")
	imap_folder_search_re = regexp.MustCompile("^* SEARCH ([0-9 ]+)$")
	imap_folder_fetch_re = regexp.MustCompile("^* ([0-9]+) FETCH \\((.*)\\)$")
	imap_message_attributes["SIZE"] = regexp.MustCompile(".*RFC822.SIZE (\\d+).*")
	imap_message_attributes["DATE"] = regexp.MustCompile(".*INTERNALDATE \\\"([^\\\"]+)\\\".*")
	imap_message_attributes["FLAGS"] = regexp.MustCompile(".*FLAGS \\(([^\\)]*)\\).*")
}

func credentials(user string, passwd string) (string, string, error) {
	reader := bufio.NewReader(os.Stdin)

	username := user
	password := passwd
	var err error
	if len(user) == 0 {
		fmt.Print("Enter Username: ")
		username, err = reader.ReadString('\n')
		if err != nil {
			return "", "", err
		}
	}

	if len(passwd) == 0 {
		fmt.Print("Enter Password: ")
		bytePassword, err := term.ReadPassword(int(syscall.Stdin))
		if err != nil {
			return "", "", err
		}
		password = string(bytePassword)
	}
	return strings.TrimSpace(username), strings.TrimSpace(password), nil
}

func get_quotas(im *imap.Dialer) (used int, total int, err error) {
	used = -1
	total = -1
	// Retrieve IMAP server capabilities
	rsp, err := im.Exec("CAPABILITY", true, 0, nil)
	if err != nil {
		fmt.Printf("Error fetching capabilities from IMAP server %+v", err)
		return
	}
	if !strings.Contains(rsp, " QUOTA ") {
		fmt.Printf("IMAP server does not support QUOTA capability\n")
		return 0, 0, errors.New("IMAP server does not support QUOTA capability")
	}
	// Retrieve IMAP server quotas
	rsp, err = im.Exec("GETQUOTAROOT INBOX", true, 0, nil)
	if err != nil {
		fmt.Printf("Error fetching quotas from IMAP server %+v", err)
		return
	}
	second_line := strings.Split(rsp, "\r\n")[1]
	mapped := imap_quota_re.FindStringSubmatch(second_line)
	if len(mapped) != 3 {
		fmt.Printf("IMAP server GETQUOTAROOT returned improperly formatted response (%s -> %d)\n", second_line, len(mapped))
		return 0, 0, errors.New("IMAP server GETQUOTAROOT returned improperly formatted response")
	}
	used, err = strconv.Atoi(mapped[1])
	if err != nil {
		fmt.Printf("IMAP server GETQUOTAROOT unable to convert used quota %s to integer (%+v)\n", mapped[1], err)
		return
	}
	total, err = strconv.Atoi(mapped[2])
	if err != nil {
		fmt.Printf("IMAP server GETQUOTAROOT unable to convert total quota %s to integer (%+v)\n", mapped[1], err)
		return
	}
	return
}

func get_folders(im *imap.Dialer) (folders []folder_name_and_flags, err error) {
	// Retrieve folders list from IMAP server
	// using on the fly parsing function
	folders = make([]folder_name_and_flags, 0)
	_, err = im.Exec(`LIST "" "*"`, false, 0, func(line []byte) (err error) {
		l := strings.Trim(string(line), "\r\n")
		mapped := imap_folder_list_re.FindStringSubmatch(l)
		if len(mapped) != 4 {
			fmt.Printf("Error decoding IMAP LIST line (%s) got %d items\n", l, len(mapped))
			err = errors.New("Error decoding IMAP LIST line")
			return
		}
		folders = append(
			folders,
			folder_name_and_flags{
				name:  mapped[3],
				flags: strings.Split(mapped[1], " "),
			})
		return
	})
	// Getting all and parse after
	// all_folders, err := im.Exec(`LIST "" "*"`, true, 0, nil)
	// all_folders_lines := strings.Split(all_folders, "\r\n")
	// fmt.Printf("GOT %d\n", len(all_folders_lines))
	if err != nil {
		fmt.Printf("Error getting folders list from IMAP server %+v", err)
		return
	}
	return
}

func examine_folder_(im *imap.Dialer, folder string) (ret int, err error) {
	// This uses the EXAMINE IMAP command (read-only mailbox)
	// as opposed to SELECT however there are no response object
	// returned so we do it manually
	//err = im.SelectFolder(folder.name)
	_, err = im.Exec(`EXAMINE "`+folder+`"`, false, 0, func(line []byte) error {
		var lerr error = nil
		l := strings.Trim(string(line), "\r\n")
		mapped := imap_folder_examine_re.FindStringSubmatch(l)
		if len(mapped) == 2 {
			nb_messages, lerr := strconv.Atoi(mapped[1])
			if lerr != nil {
				fmt.Printf("IMAP server EXAMINE unable to convert existing messages number %s to integer (%+v)\n", mapped[1], lerr)
				return lerr
			}
			ret = nb_messages
		}
		return lerr
	})
	im.Folder = folder
	return
}

func search_all_folder(im *imap.Dialer) (min, max int, err error) {
	min = 1000000000
	max = 1
	_, err = im.Exec(`SEARCH ALL`, false, 0, func(line []byte) error {
		var lerr error = nil
		l := strings.Trim(string(line), "\r\n")
		mapped := imap_folder_search_re.FindStringSubmatch(l)
		if len(mapped) != 2 {
			fmt.Printf("IMAP server SEARCH ALL parse response properly: (%d) %s\n", len(mapped), l)
			return errors.New("IMAP server SEARCH ALL parse response properly")
		}
		for _, id := range strings.Split(mapped[1], " ") {
			int_id, lerr := strconv.Atoi(id)
			if lerr != nil {
				fmt.Printf("IMAP server SEARCH ALL unable to convert existing messages number %s to integer (%+v)\n", id, lerr)
				return lerr
			}
			if int_id < min {
				min = int_id
			} else if int_id > max {
				max = int_id
			}
		}
		//fmt.Printf("GOT %s\n", l)
		return lerr
	})
	return
}

func fetch_folder_messages(im *imap.Dialer, msg_set string, msg_details string, ret_folder_stats *folder_stats) (err error) {
	_, err = im.Exec(`FETCH `+msg_set+` `+msg_details, false, 0, func(line []byte) error {
		var lerr error = nil
		l := strings.Trim(string(line), "\r\n")
		mapped := imap_folder_fetch_re.FindStringSubmatch(l)
		if len(mapped) != 3 {
			fmt.Printf("IMAP server FETCH unable to parse response properly: (%d) %s\n", len(mapped), l)
			return errors.New("IMAP server FETCH unable to parse response properly")
		}
		int_id, lerr := strconv.Atoi(mapped[1])
		if lerr != nil {
			fmt.Printf("IMAP server FETCH parse response unable to convert existing messages number %s to integer (%+v)\n", mapped[1], lerr)
			return lerr
		}
		msg_infos := message_infos{
			id:     int_id,
			size:   0,
			date:   time.Date(1980, 1, 1, 0, 0, 0, 0, time.Local),
			flags:  []string{},
			folder: ret_folder_stats.name,
		}
		for key, regxp := range imap_message_attributes {
			a_mapped := regxp.FindStringSubmatch(mapped[2])
			if len(a_mapped) != 2 {
				fmt.Printf("IMAP server FETCH unable to parse response properly: (%d) (%s) %s\n", len(a_mapped), key, mapped[2])
				return errors.New("IMAP server FETCH unable to parse response properly (attributes)")
			}
			if key == "FLAGS" {
				msg_infos.flags = strings.Split(a_mapped[1], " ")
				// TODO: see if we can also sum for NonJunk and \\Answered
				if !strings.Contains(a_mapped[1], "\\Seen") {
					ret_folder_stats.unreads += 1
				}
			} else if key == "DATE" {
				date_time, lerr := time.Parse("02-Jan-2006 15:04:05 +0000", a_mapped[1])
				if lerr != nil {
					fmt.Printf("IMAP server FETCH parse response unable to convert existing messages date %s (%+v)\n", a_mapped[1], lerr)
					return lerr
				}
				msg_infos.date = date_time
			} else if key == "SIZE" {
				int_size, lerr := strconv.Atoi(a_mapped[1])
				if lerr != nil {
					fmt.Printf("IMAP server FETCH parse response unable to convert existing messages size %s to integer (%+v)\n", a_mapped[1], lerr)
					return lerr
				}
				msg_infos.size = int_size
				ret_folder_stats.size += int_size
			} else {
				fmt.Printf("IMAP server FETCH parse response properly: (%s) %s\n", key, mapped[2])
			}
		}
		return lerr
	})
	return
}

func folder_infos(im *imap.Dialer, folder folder_name_and_flags) (ret folder_stats, err error) {
	lflags := mapset.NewSet[string]()
	for _, f := range folder.flags {
		lflags.Add(f)
	}
	special_folder := lflags.Intersect(special_folder_flags)
	if special_folder.Cardinality() != 0 {
		fmt.Printf("IMAP folder %s not processed [%+v]\n", folder.name, special_folder)
		return
	}
	unknown_folder_flags := lflags.Difference(known_folder_flags)
	if unknown_folder_flags.Cardinality() != 0 {
		fmt.Printf("IMAP folder %s got unknown flag(s) [%+v]\n", folder.name, unknown_folder_flags)
	}
	rname, err := UTF7Decode(folder.name)
	if err != nil {
		fmt.Printf("Error decoding IMAP folder name in UTF-7 %s %+v\n", folder.name, err)
		return
	}
	ret = folder_stats{
		name:     rname,
		messages: 0,
		unreads:  0,
		size:     0,
		quota:    0.0,
		contents: make([]message_infos, 0),
	}
	nb_msgs, err := examine_folder_(im, folder.name)
	if err != nil {
		fmt.Printf("Error selecting IMAP folder %s %+v\n", rname, err)
		return
	}
	ret.messages = nb_msgs
	// No further computation required for empty folder
	if ret.messages == 0 {
		return
	}
	min_msg_id, max_msg_id, err := search_all_folder(im)
	if err != nil {
		fmt.Printf("Error searching IMAP folder %s %+v\n", rname, err)
		return
	}
	msg_set := fmt.Sprintf("%d:%d", min_msg_id, max_msg_id)
	err = fetch_folder_messages(im, msg_set, "FAST", &ret)
	if err != nil {
		fmt.Printf("Error fetching IMAP folder %s %+v\n", rname, err)
	}
	return
}

func main() {
	initialize_env_and_cmd_line_config()
	// Initialize global vars
	imap_server = viper.GetString("server")
	imap_details = viper.GetBool("details")
	no_trace = viper.GetBool("no_trace")

	initialize_globals()

	usr, passwd, _ := credentials(viper.GetString("user"), viper.GetString("password"))

	imap.Verbose = viper.GetBool("debug")
	// Defaults to 10 => here we ask to never retry
	imap.RetryCount = 0
	im, err := imap.New(usr, passwd, imap_server, 993)
	if err != nil {
		fmt.Printf("Error connecting to IMAP server %+v", err)
		os.Exit(1)
	}
	defer im.Close()
	quotas_used, quotas_total, err := get_quotas(im)
	if err != nil {
		fmt.Printf("Error fetching quotas from IMAP server %+v\n", err)
		os.Exit(1)
	}
	fmt.Printf("quotas_used %d, quotas_total %d\n", quotas_used, quotas_total)
	folders, err := get_folders(im)
	if err != nil {
		fmt.Printf("Error fetching folders list from IMAP server %+v\n", err)
		os.Exit(1)
	}
	total_messages := 0
	total_unreads := 0
	total_size := 0
	all_folders := make([]folder_stats, 0)
	// May be duplicate list of all messages like in Python
	// version
	// all_messages := make([]message_infos, 0)
	for _, folder := range folders {
		f_infos, err := folder_infos(im, folder)
		if err != nil {
			fmt.Printf("Error getting folder details on %s\n", folder)
		}
		all_folders = append(all_folders, f_infos)
		if f_infos.messages > 0 {
			total_messages += f_infos.messages
			total_size += f_infos.size
			total_unreads += f_infos.unreads
			// all_messages = append(all_messages, f_infos.contents...)
		}
	}
	fmt.Printf("Total messages: %d\n", total_messages)
	fmt.Printf("Total unreads: %d\n", total_unreads)
	fmt.Printf("Total size: %d\n", total_size)
}
