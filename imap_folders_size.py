#!/usr/bin/env python

# LOGNAME=my-email

import sys, os, string, imaplib, getpass, pdb

def print_folder_size(folder, nb, size):
	fmt = "%16s %-50s %5s"
	print fmt % (size, folder, nb)

def folder_size(M, folder_entry):
	fs = 0
	nb = '0'
	folder_items = folder_entry.split()
	# Select the desired folder
	mbx = string.join(folder_items[2:])
	pdb.set_trace()
	result, nb = M.select(mbx, readonly=1)
	if result != 'OK':
		print 'IMAP folder select returned %s' % (result)
		return -1, 0
	# Go through all the messages in the selected folder
	typ, msg = M.search(None, 'ALL')
	# Find the first and last messages
	m = [int(x) for x in msg[0].split()]
	if m:
		m.sort()
		msgset = "%d:%d" % (m[0], m[-1])
		result, msizes = M.fetch(msgset, "(UID RFC822.SIZE)")
		if result != 'OK':
			print 'IMAP messages sizes returned %s' % (result)
			return -1, 0
		for msg in msizes:
			fs += int(msg.split()[-1].replace(')', ''))
	print_folder_size(mbx, int(nb[0]), fs) 
	return int(nb[0]), fs

imap_server = "imap.free.fr"
#imap_server = "imap.gmail.com"

# Open a connection to the IMAP server using SSL and proper port
M = imaplib.IMAP4_SSL(imap_server, 993)
# User is retrieved from LOGNAME environment variable
# password is asked on command line
(user, passwd) = (getpass.getuser(), getpass.getpass('Enter password for user %s > ' % os.environ['LOGNAME']))

try:
	M.login(user, passwd)
except imaplib.IMAP4.error as e:
	print 'Error: %s' % (e)
	sys.exit(1)

# The list of all folders 
result, folders = M.list()
if result != 'OK':
	print 'IMAP folder list returned %s' % (result)
	sys.exit(1)

print_folder_size("Folder", "# Msg", "Size") 

nmessages_total = 0
size_total = 0

#pdb.set_trace()
for folder in folders:
	fcount, fsize = folder_size(M, folder)
	nmessages_total += fcount
	size_total += fsize

print_folder_size("Sum", nmessages_total, size_total) 

# Close the connection 
M.logout() 
