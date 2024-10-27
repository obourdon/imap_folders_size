package main

import (
       "fmt"

       "github.com/spf13/viper"
       flag "github.com/spf13/pflag"
)

var globalVar int = 42

func main() {
     // Environment parsing
     viper.AutomaticEnv() // read value ENV variable
     // Set default value
     viper.SetEnvPrefix("imap")
     viper.SetDefault("server", "imap.gmail.com")
     viper.SetDefault("details", false)

     // Command line arguments parsing
     flag.String("server", "tooto", "help message for server")
     flag.Bool("details", false, "help message for details")
     flag.Parse()
     viper.BindPFlags(flag.CommandLine)

     // Declare vars
     imap_server := viper.GetString("server")
     imap_details := viper.GetBool("details")

     fmt.Println("---------- Example ----------")
     fmt.Printf("server (%T): %s\n", imap_server, imap_server)
     fmt.Printf("details (%T): %#v\n", imap_details, imap_details)
}
