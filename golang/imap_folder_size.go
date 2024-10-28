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

	"golang.org/x/term"

	"github.com/BrianLeishman/go-imap"
	mapset "github.com/deckarep/golang-set/v2"
	flag "github.com/spf13/pflag"
	"github.com/spf13/viper"
)

var imap_server string
var imap_details bool
var no_trace bool

var special_folder_flags = mapset.NewSet[string]()
var known_folder_flags = mapset.NewSet[string]()

var imap_folder_re *regexp.Regexp
var imap_quota_re *regexp.Regexp
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
	special_folder_flags.Add("Noselect")
	special_folder_flags.Add("All")
	special_folder_flags.Add("Important")

	known_folder_flags.Add("HasNoChildren")
	known_folder_flags.Add("HasChildren")
	known_folder_flags.Add("Drafts")
	known_folder_flags.Add("Sent")
	known_folder_flags.Add("Junk")
	known_folder_flags.Add("Trash")
	known_folder_flags.Add("Flagged")
	known_folder_flags = known_folder_flags.Union(special_folder_flags)

	// Regular expressions
	imap_folder_re = regexp.MustCompile("^\\([^\\)]*\\) (.*)$")
	imap_quota_re = regexp.MustCompile("^* QUOTA [^\\(]+ \\(STORAGE (\\d+) (\\d+)\\)$")
	imap_message_attributes["ID"] = regexp.MustCompile("^(\\d+) \\((.*)\\)$")
	imap_message_attributes["SIZE"] = regexp.MustCompile(".*RFC822.SIZE (\\d+).*")
	imap_message_attributes["DATE"] = regexp.MustCompile(".*INTERNALDATE \\\"([^\\\"]+)\\\".*")
	imap_message_attributes["FLAGS"] = regexp.MustCompile(".*FLAGS \\(([^\\)]+)\\).*")
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
}
