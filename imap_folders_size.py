#!/usr/bin/env python3

# Press ⌃R to execute it or replace it with your code.
# Press Double ⇧ to search everywhere for classes, files, tool windows, actions, and settings.


# LOGNAME=my-email
# LOGPASSWD=xxxxx

import email, getpass, imapclient, imaplib, os, pdb, re, sys, tabulate
from datetime import datetime
import numpy as np

imap_folder_re = re.compile(r"^\([^)]*\) (.*)$")
imap_quota_re = re.compile(r"^\"[^\"]*\" \(STORAGE (\d+) (\d+)\)$")

imap_server = "imap.free.fr"
#imap_server = "imap.gmail.com"


def human_readable_size(size, suffix='B', decimal_places=1, units_offset=0):
    offset = ' '*units_offset
    for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']:
        if size < 1024.0 or unit == 'Y':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f}{offset}{unit}{suffix}"


def folder_real_name(folder, decoded=True):
    if decoded:
        return imapclient.imap_utf7.decode(folder.encode())
    return folder


def list_append(v, name="size", extras={}):
    cmd="message_" + name + "s.append({**extras, **{'VALUE': v}})"
    eval(cmd)


def message_subject_from_to(msg):
    msg_id = msg.get('ID')
    mbx = msg.get('FOLDER')
    if mbx == None or msg_id == None:
        print('Unable to retrieve folder for message %s in %s' % (msg.get('ID'), msg.get('FOLDER')))
        return "(None)", "(None)", "(None)"
    result, nb = M.select(mbx, readonly=1)
    if result != 'OK':
        print('%s IMAP folder select returned %s' % (mbx, result))
        return "(None)", "(None)", "(None)"
    result, msg_data = M.fetch(str(msg_id), "(RFC822)")
    if result != 'OK':
        print('%s IMAP folder fetch %s returned %s' % (mbx, msg_id, result))
        return "(None)", "(None)", "(None)"
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            try:
                msg = email.message_from_string(response_part[1].decode('utf-8'))
                msg_from = str(email.header.make_header(email.header.decode_header(msg['From'])))
                msg_to = str(email.header.make_header(email.header.decode_header(msg['To'])))
                msg_subject = str(email.header.make_header(email.header.decode_header(msg['Subject'])))
                return msg_from, msg_to, msg_subject
            except Exception as e:
                print('%s IMAP folder message %s can not decode: %s' % (mbx, msg_id, e))
    return "(None)", "(None)", "(None)"


def folder_size(M, folder_entry):
    global message_sizes, message_dates, min_max
    fs = 0
    nb = '0'
    imap_folder_match = imap_folder_re.match(str(folder_entry, 'utf-8'))
    if imap_folder_match == None:
        print('IMAP folder %s does not match regexp' % (folder_entry))
        return 0, 0
    folder_items = imap_folder_match.group(1).split()
    # Select the desired folder
    mbx = '"' + ' '.join(map(lambda x: x.strip('"'), folder_items[1:])) + '"'
    result, nb = M.select(mbx, readonly=1)
    if result != 'OK':
        print('%s IMAP folder select returned %s' % (mbx, result))
        return -1, 0
    # Go through all the messages in the selected folder
    typ, msg = M.search(None, 'ALL')
    # Find the first and last messages
    m = [int(x) for x in msg[0].split()]
    if m:
        m.sort()
        msgset = "%d:%d" % (m[0], m[-1])
        result, msizes = M.fetch(msgset, "(INTERNALDATE RFC822.SIZE)")
        if result != 'OK':
            print('IMAP messages sizes returned %s' % (result))
            return -1, 0
        for msg in map(lambda x: dict(list(zip(*[iter(re.sub(r'([1-9][0-9]*) \((.*)\)', r'ID,\1,\2', str(x.replace(b'"',b'').replace(b' RFC822.SIZE ', b',SIZE,').replace(b'INTERNALDATE ', b'DATE,'), 'utf-8')).split(','))] * 2))), msizes):
            msg_size = int(msg['SIZE'])
            msg_date = None
            try:
              msg_date = datetime.strptime(msg['DATE'], '%d-%b-%Y %H:%M:%S %z')
              list_append(msg_date, name="date", extras={'ID': int(msg['ID']), 'FOLDER': mbx, 'SIZE': msg_size})
            except ValueError as e:
                print('IMAP message date decoding error: %s %s' % (msg[1], e))
            list_append(msg_size, name="size", extras={'ID': int(msg['ID']), 'FOLDER': mbx, 'DATE': msg_date})
            fs += msg_size
    return {'name': folder_real_name(mbx.strip('"')), 'messages': int(nb[0]), 'size': fs}


def env_or_tty_passwd():
    lpasswd = os.getenv("LOGPASSWD")
    if lpasswd == None:
        lpasswd = getpass.getpass('Enter password for user %s > ' % os.environ['LOGNAME'])
    return lpasswd


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    # Open a connection to the IMAP server using SSL and proper port
    M = imaplib.IMAP4_SSL(imap_server, 993)
    # User is retrieved from LOGNAME environment variable
    # password is asked on command line or environment variable
    (user, passwd) = (getpass.getuser(), env_or_tty_passwd())

    try:
        M.login(user, passwd)
    except imaplib.IMAP4.error as e:
        print('IMAP Login error: %s' % (e))
        sys.exit(1)

    # List server capabilities
    try:
        capabilities_rsp = M.capability()
        if (capabilities_rsp[0] != 'OK'):
            raise Exception('Unable to retrieve IMAP server capabilities')
        if " QUOTA " in str(capabilities_rsp[1][0]):
            quota_rsp = M.getquotaroot("INBOX")
            if (quota_rsp[0] != 'OK'):
                raise Exception('Unable to retrieve IMAP server quotas')
            quota_infos = imap_quota_re.match(str(quota_rsp[1][1][0], 'utf-8'))
            if quota_infos == None:
                raise Exception('Unable to parse IMAP server quotas')
            quota_used, quota_total = int(quota_infos.group(1)), int(quota_infos.group(2))
    except imaplib.IMAP4.error as e:
        print('IMAP capabilities error: %s' % (e))
        sys.exit(1)
    except Exception as e:
        print('IMAP capabilities error: %s' % (e))

    # The list of all folders
    result, folders = M.list()
    if result != 'OK':
        print('IMAP folder list returned %s' % (result))
        sys.exit(1)

    nmessages_total = 0
    size_total = 0

    imap_folders = []
    min_max = {'size_min': None, 'size_max': None, 'date_min': None, 'date_max': None}
    message_sizes = []
    message_dates = []
    #pdb.set_trace()
    for folder in folders:
        folder_infos = folder_size(M, folder)
        folder_stats = [folder_infos['name'], folder_infos['messages'], folder_infos['size']]
        if quota_used != None and quota_used != 0:
            folder_stats.append((100.0 * folder_infos['size']) / (1024 * quota_used))
        imap_folders.append(folder_stats)
        nmessages_total += imap_folders[-1][1]
        size_total += imap_folders[-1][2]

    summary = ["Sum", nmessages_total, size_total]
    hfields = ["Folder", "# Msg", "Size"]
    if quota_used != None and quota_used != 0:
        hfields.append("%")
        summary.append(100)
    imap_folders.append(summary)
    print(tabulate.tabulate(imap_folders, headers=hfields, floatfmt=".2f"))
    if quota_used != None and quota_total != None:
        print("\nQuotas Used: %s Total: %s Usage: %.2f%%" % (human_readable_size(quota_used*1024), human_readable_size(quota_total*1024), (100*quota_used)/quota_total))
    sdata = np.array(list(map(lambda x: x.get("VALUE"), message_sizes)))
    ddata = np.array(list(map(lambda x: x.get("VALUE"), message_dates)))
    print("\nMessage sizes: [{} - {}]".format(sdata.min(), sdata.max()))
    print("\nMessage dates: [{} - {}]".format(ddata.min(), ddata.max()))
    over95percent = int(sdata.mean() + 2 * sdata.std())
    print("\nMessages over {} (upper 95% quartile):\n".format(human_readable_size(over95percent)))
    to_save = 0
    big_messages = sorted(list(filter(lambda x: x.get("VALUE", 0) > over95percent, message_sizes)), key=lambda x: x.get("VALUE"))
    biggest = []
    for msg in big_messages:
        msg_from, msg_to, msg_subject = message_subject_from_to(msg)
        biggest.append([msg.get("ID"), human_readable_size(msg.get("VALUE")), msg.get("DATE"), folder_real_name(msg.get("FOLDER").strip('"')), msg_from, msg_subject])
        to_save += msg.get("VALUE")
    print(tabulate.tabulate(biggest, headers=["ID", "Size", "Date", "Folder", "From", "Subject"]))
    print("You can save %s (%.2f%%) by cleaning up the %d biggest messages\n" % (human_readable_size(to_save), ((100*to_save)/(1024*quota_used)), len(big_messages)))
    # Close the connection
    M.logout()

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
