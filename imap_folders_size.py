#!/usr/bin/env python3

# LOGNAME=my-email
# LOGPASSWD=xxxxx

import pdb

from datetime import datetime
import email
import getpass
import imapclient
import imaplib
import numpy as np
import os
import re
import sys
import tabulate


# Some regular expressions for IMAP response decoding
# TODO: better use r"^\([^)]*\) \"([^\"]+)\" \"(.*)\"$")
# to segment flags, separator, folder_name
imap_folder_re = re.compile(r"^\([^)]*\) (.*)$")
imap_quota_re = re.compile(r"^\"[^\"]*\" \(STORAGE (\d+) (\d+)\)$")

# Define some constant for IMAP folders flags
special_folder_flags = set((
    'Noselect',
    'All',
    'Important'
    ))
known_folder_flags = set((
    'HasNoChildren',
    'HasChildren',
    'Drafts',
    'Sent',
    'Junk',
    'Trash',
    'Flagged'))
known_folder_flags.update(special_folder_flags)

# Other globalss
# imap_server = "imap.free.fr"
imap_server = "imap.gmail.com"


def trace_msg(msg):
    if os.getenv("NO_TRACE"):
        return
    print(msg)


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
    cmd = "message_" + name + "s.append({**extras, **{'VALUE': v}})"
    eval(cmd)


def message_subject_from_to(msg):
    msg_id = msg.get('ID')
    mbx = msg.get('FOLDER')
    if not mbx or not msg_id:
        print(f"Unable to retrieve folder for message {msg.get('ID')} in {msg.get('FOLDER')}")
        return "(None)", "(None)", "(None)"
    result, nb = XXX.select(mbx, readonly=1)
    if result != 'OK':
        print(f"{mbx} IMAP folder select returned {result} (message_subject_from_to)")
        return "(None)", "(None)", "(None)"
    # Only retrieve mail headers for faster computation
    result, msg_data = M.fetch(str(msg_id), "(RFC822.HEADER)")
    if result != 'OK':
        print(f"{mbx} IMAP folder fetch {msg_id} returned {result}")
        return "(None)", "(None)", "(None)"
    # Only keep the proper typed results
    for response_part in filter(lambda x: isinstance(x, tuple), msg_data):
        # Should always be true but for more safety
        if isinstance(response_part, tuple):
            try:
                utf8_msg = email.message_from_string(response_part[1].decode('utf-8'))
                # The IMAPlib module can return empty From/To/Subject headers therefore use get instead of dict keys
                msg_from = str(email.header.make_header(email.header.decode_header(utf8_msg.get('From', '**NONE**'))))
                msg_to = str(email.header.make_header(email.header.decode_header(utf8_msg.get('To', '**NONE**'))))
                msg_subject = str(email.header.make_header(email.header.decode_header(utf8_msg.get('Subject', '**NONE**'))))
                return msg_from, msg_to, msg_subject
            except Exception as e:
                print(f"{mbx} IMAP folder message {msg_id} can not decode: {e}")
    return "(None)", "(None)", "(None)"


def folder_size(
    cnx: imaplib.IMAP4_SSL,
    folder_entry: bytes,
        ) -> tuple[dict[str, str | int], Exception]:
    # TODO: prevent usage of globals
    global message_sizes, message_dates, min_max
    fs = 0
    nb = '0'
    # folder_entry.decode().split(' "/" ')
    # 2 element tuple
    imap_folder_match = imap_folder_re.match(str(folder_entry, 'utf-8'))
    if not imap_folder_match:
        return {}, Exception(f"IMAP folder {folder_entry} does not match regexp")
    folder_items = imap_folder_match.group(1).split()
    # str(folder_entry, 'utf-8').split(' "/" ') same as folder_entry.decode().split(' "/" ')
    folder_flags = eval(','.join(folder_entry.decode().split(' "/" ')[0].replace('\\','').split(' ')).replace('(','("').replace(',','","').replace(')','",)'))
    s1 = set(folder_flags)
    special_folder = s1.intersection(special_folder_flags)
    # Folder name only
    mbx = '"' + ' '.join(map(lambda x: x.strip('"'), folder_items[1:])) + '"'
    # Folder is not selectable or is tagged with special meaning
    if len(special_folder) > 0:
        return {}, Exception(f"{mbx} IMAP folder not processed {special_folder} (folder_size)")
    unknown_folder_flags = s1.difference(known_folder_flags)
    # TODO: see how to report this better upstream
    if len(unknown_folder_flags) > 0:
        print(f"{mbx} IMAP folder got unknown flag(s) -> {unknown_folder_flags}")
    # Select the desired folder
    result, nb = cnx.select(mbx, readonly=1)
    if result != 'OK':
        return {}, Exception(f"{mbx} IMAP folder select returned {result} (folder_size)")
    # TODO: do some meaningful computation with flags
    # flags = cnx.response('FLAGS')
    # RECENT response element does not seem to be supported (anymore?)
    # recents = cnx.response('RECENT')
    unread_emails = 0
    # No need to further call IMAP server API
    if int(nb[0]) == 0:
        return {
            'name': folder_real_name(mbx.strip('"')),
            'messages': 0,
            'unread': 0,
            'size': 0,
            }, None
    # and/or verify that int(nb[0]) == len(msg[0].split())
    # Go through all the messages in the selected folder
    typ, msgs = cnx.search(None, 'ALL')
    # TODO: verify that typ == 'OK'
    # Find the first and last messages
    m = [int(x) for x in msgs[0].split()]
    if m:
        m.sort()
        msgset = f"{m[0]}:{m[-1]}"
        result, msizes = cnx.fetch(msgset, "(INTERNALDATE RFC822.SIZE)")
        if result != 'OK':
            return {}, Exception(f"IMAP messages sizes returned {result}")
        # TODO: check that len(msizes) == int(nb[0])
        # TODO: may be find a more clever way to properly compute the
        # size and date whatever the order they are returned in
        for msg in map(
            lambda x: dict(
                list(
                    zip(
                        *[iter(re.sub(
                            r'([1-9][0-9]*) \((.*)\)', r'ID,\1,\2',
                            str(
                                x.replace(b'"', b'')
                                .replace(b' RFC822.SIZE ', b',SIZE,')
                                .replace(b' INTERNALDATE ', b',DATE,')
                                .replace(b'RFC822.SIZE ', b'SIZE,')
                                .replace(b'INTERNALDATE ', b'DATE,'), 'utf-8')
                            ).split(','))] * 2
                        )
                    )
                ),
            msizes
        ):
            msg_size = int(msg['SIZE'])
            msg_date = None
            try:
                msg_date = datetime.strptime(
                    msg['DATE'],
                    '%d-%b-%Y %H:%M:%S %z'
                    )
                list_append(
                    msg_date,
                    name="date",
                    extras={
                        'ID': int(msg['ID']),
                        'FOLDER': mbx,
                        'SIZE': msg_size
                        })
            except ValueError as e:
                # TODO: see hoe to report this better upstream
                print(f"IMAP message date decoding error: {msg[1]} {e}")
            list_append(
                msg_size,
                name="size",
                extras={
                    'ID': int(msg['ID']),
                    'FOLDER': mbx,
                    'DATE': msg_date
                    })
            fs += msg_size
    return {
        'name': folder_real_name(mbx.strip('"')),
        'messages': int(nb[0]),
        'unread': unread_emails,
        'size': fs
        }, None


def env_or_tty_passwd() -> str:
    return os.getenv("LOGPASSWD") or getpass.getpass(
        f"Enter password for user {os.environ['LOGNAME']} > "
        )


def login(
    svr: str,
    port: int = 993,
    user: str = None,
    password: str = None,
        ) -> imaplib.IMAP4_SSL:
    # Open a connection to the IMAP server using SSL and proper port
    cnx = imaplib.IMAP4_SSL(svr, port)
    try:
        cnx.login(user, password)
    except imaplib.IMAP4.error as e:
        # TODO: new exception type inheriting from IMAP4
        raise imaplib.IMAP4.error(f"IMAP Login error: {e}")
    except Exception as e:
        raise Exception(f"IMAP Login error. Unknown exception: {e}")
    return cnx


def get_quotas(
    cnx: imaplib.IMAP4_SSL,
        ) -> tuple[int, int]:
    try:
        # List server capabilities
        capabilities_rsp = cnx.capability()
        if (capabilities_rsp[0] != 'OK'):
            raise Exception('Unable to retrieve IMAP server capabilities')
        if " QUOTA " in str(capabilities_rsp[1][0]):
            quota_rsp = cnx.getquotaroot("INBOX")
            if (quota_rsp[0] != 'OK'):
                raise Exception('Unable to retrieve IMAP server quotas')
            quota_infos = imap_quota_re.match(str(quota_rsp[1][1][0], 'utf-8'))
            if not quota_infos:
                raise Exception('Unable to parse IMAP server quotas')
            return int(quota_infos.group(1)), int(quota_infos.group(2))
    except imaplib.IMAP4.error as e:
        raise Exception(f"IMAP Quotas erro: {e}")
    except Exception as e:
        raise Exception(f"IMAP Quotas error. Unknown exception: {e}")


def get_folders(
    cnx: imaplib.IMAP4_SSL,
        ) -> list[bytes]:
    # The list of all folders
    result, folders = cnx.list()
    if result != 'OK':
        raise Exception(f"IMAP folder list returned {result}")
    return folders


def error_or_warning(cond: bool) -> str:
    if cond:
        return "ERROR"
    return "WARNING"


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    try:
        # User is retrieved from LOGNAME environment variable
        # password is asked on command line or environment variable
        (usr, passwd) = (getpass.getuser(), env_or_tty_passwd())
        cnx = login(imap_server, user=usr, password=passwd)
        quota_used, quota_total = get_quotas(cnx)
        folders = get_folders(cnx)
    except imaplib.IMAP4.error as e:
        print(f"IMAP error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Exception error: {e}")
        sys.exit(2)

    nmessages_total = 0
    nunread_total= 0
    size_total = 0

    imap_folders = []
    min_max = {
        'size_min': None,
        'size_max': None,
        'date_min': None,
        'date_max': None
        }
    message_sizes = []
    message_dates = []
    trace_msg('FOLDERS ACQUIRED')
    for folder in folders:
        folder_infos, ex = folder_size(cnx, folder)
        if ex:
            print(f'{error_or_warning(len(folder_infos) > 0)}: got {ex}')
        if folder_infos.get('name'):
            folder_stats = [
                folder_infos['name'],
                folder_infos['messages'],
                folder_infos['unread'],
                folder_infos['size'],
                ]
            if quota_used:
                folder_stats.append(
                    (100.0 * folder_infos['size'])
                    / (1024 * quota_used))
            imap_folders.append(folder_stats)
            nmessages_total += imap_folders[-1][1]
            size_total += imap_folders[-1][3]
            nunread_total += imap_folders[-1][2]
    trace_msg('FOLDERS PROCESSED')
    summary = ["Sum", nmessages_total, nunread_total, size_total]
    hfields = ["Folder", "# Msg", "# Unread", "Size"]
    if quota_used:
        hfields.append("%")
        summary.append(100)
    imap_folders.append(summary)
    print(tabulate.tabulate(imap_folders, headers=hfields, floatfmt=".2f"))
    if quota_used and quota_total:
        print(f"\nQuotas Used: {human_readable_size(quota_used*1024)} Total: {human_readable_size(quota_total*1024)} Usage: {(100*quota_used)/quota_total:.2f}%")
        if 'gmail.com' in imap_server:
            print(f'Email related: Total messages size: {human_readable_size(size_total)} Used%: {(100*size_total)/(1024*quota_used):.2f}% Total%: {(100*size_total)/(1024*quota_total):.2f}%')
    trace_msg('BASIC STATS PRINTED')
    sdata = np.array(list(map(lambda x: x.get("VALUE"), message_sizes)))
    ddata = np.array(list(map(lambda x: x.get("VALUE"), message_dates)))
    print(f"\nMessage sizes: [{sdata.min()} - {sdata.max()}]")
    print(f"\nMessage dates: [{ddata.min()} - {ddata.max()}]")
    over95percent = int(sdata.mean() + 2 * sdata.std())
    print(f"\nMessages over {human_readable_size(over95percent)} (upper 95% quartile):\n")
    to_save = 0
    big_messages = sorted(list(filter(lambda x: x.get("VALUE", 0) > over95percent, message_sizes)), key=lambda x: x.get("VALUE"))
    biggest = []
    for msg in big_messages:
        msg_from, msg_to, msg_subject = message_subject_from_to(msg)
        biggest.append([msg.get("ID"), human_readable_size(msg.get("VALUE")), (100.0 * msg.get("VALUE")) / (1024 * quota_used), msg.get("DATE"), folder_real_name(msg.get("FOLDER").strip('"')), msg_from, msg_subject])
        to_save += msg.get("VALUE")
    print(tabulate.tabulate(biggest, headers=["ID", "Size", "%", "Date", "Folder", "From", "Subject"], floatfmt=".2f"))
    print(f"\nYou can save {human_readable_size(to_save)} ({((100*to_save)/(1024*quota_used)):.2f}%) by cleaning up the {len(big_messages)} biggest messages\n")
    # Close the connection
    cnx.logout()
