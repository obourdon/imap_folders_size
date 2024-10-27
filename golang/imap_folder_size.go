package main

import (
       "bufio"
       "fmt"
       "os"
       "regexp"
       "strings"
       "syscall"
       "golang.org/x/term"

       "github.com/spf13/viper"
       flag "github.com/spf13/pflag"
       mapset "github.com/deckarep/golang-set/v2"
)

var imap_server  string
var imap_details bool
var no_trace     bool

var special_folder_flags = mapset.NewSet[string]()
var known_folder_flags   = mapset.NewSet[string]()

var imap_folder_re *regexp.Regexp
var imap_quota_re  *regexp.Regexp
var imap_message_attributes = make(map[string]*regexp.Regexp, 4)

func initialize_env_and_cmd_line_config() {
     // Environment parsing
     viper.AutomaticEnv() // read value ENV variable
     // Set default value
     viper.SetEnvPrefix("imap")
     viper.SetDefault("server", "imap.gmail.com")
     viper.SetDefault("details", false)
     viper.SetDefault("no_trace", false)

     // Command line arguments parsing
     flag.String("server", "imap.gmail.com", "the IMAP server DNS name or IP address")
     flag.Bool("details", false, "get IMAP messages details (default to false)")
     flag.Bool("no_trace", false, "disable deep tracing (default to false)")
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
     imap_quota_re = regexp.MustCompile("^\\\"[^\\\"]*\\\" \\(STORAGE (\\d+) (\\d+)\\)$")
     imap_message_attributes["ID"] = regexp.MustCompile("^(\\d+) \\((.*)\\)$")
     imap_message_attributes["SIZE"] = regexp.MustCompile(".*RFC822.SIZE (\\d+).*")
     imap_message_attributes["DATE"] = regexp.MustCompile(".*INTERNALDATE \\\"([^\\\"]+)\\\".*")
     imap_message_attributes["FLAGS"] = regexp.MustCompile(".*FLAGS \\(([^\\)]+)\\).*")
}

func credentials() (string, string, error) {
    reader := bufio.NewReader(os.Stdin)

    fmt.Print("Enter Username: ")
    username, err := reader.ReadString('\n')
    if err != nil {
        return "", "", err
    }

    fmt.Print("Enter Password: ")
    bytePassword, err := term.ReadPassword(int(syscall.Stdin))
    if err != nil {
        return "", "", err
    }

    password := string(bytePassword)
    return strings.TrimSpace(username), strings.TrimSpace(password), nil
}

func main() {
     initialize_env_and_cmd_line_config()
     // Initialize global vars
     imap_server = viper.GetString("server")
     imap_details = viper.GetBool("details")
     no_trace = viper.GetBool("no_trace")

     fmt.Println("---------- Example ----------")
     fmt.Printf("server (%T): %s\n", imap_server, imap_server)
     fmt.Printf("details (%T): %#v\n", imap_details, imap_details)
     fmt.Printf("no_trace (%T): %#v\n", no_trace, no_trace)

     initialize_globals()
     fmt.Printf("Special folders: %+v\n", special_folder_flags)
     fmt.Printf("Known folders: %+v\n", known_folder_flags)

     usr, passwd, _ := credentials()
     fmt.Printf("Username: %s, Password: %s\n", usr, passwd)
}
