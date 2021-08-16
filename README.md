# imap_folders_size
List all IMAP folders with size in number of messages and size in bytes

To use, followthe instructions hereafter:

```
# Create Python virtual environment
$ python3 -m venv ~/.venvs/imap
# Activate this environment
$ . ~/.venvs/imap/bin/activate
# Install Python packages requirements
$ pip3 install -r requirements.txt
# Optionally update pip3
$ python3 -m pip install --upgrade pip
# Run the program using the proper email account
$ LOGNAME=myemail-without-the@free.fr ./imap_folders_size.py
```
