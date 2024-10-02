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
imap_message_attributes = {
    'ID': re.compile(r"^(\d+) \((.*)\)$"),
    'SIZE': re.compile(r".*RFC822.SIZE (\d+).*"),
    'DATE': re.compile(r".*INTERNALDATE \"([^\"]+)\".*"),
    'FLAGS': re.compile(r".*FLAGS \(([^\)]+)\).*"),
}

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


def message_subject_from_to(
    cnx: imaplib.IMAP4_SSL,
    msg: dict[str, str],
    ) -> tuple[str, str, str]:
    msg_id = msg.get('id')
    mbx = msg.get('folder')
    if not mbx or not msg_id:
        print(f"Unable to retrieve folder for message {msg.get('id')} in {msg.get('folder')}")
        return "(None)", "(None)", "(None)"
    result, nb = cnx.select(mbx, readonly=1)
    if result != 'OK':
        print(f"{mbx} IMAP folder select returned {result} (message_subject_from_to)")
        return "(None)", "(None)", "(None)"
    # Only retrieve mail headers for faster computation
    result, msg_data = cnx.fetch(str(msg_id), "(RFC822.HEADER)")
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


def parse_message_basic_attributes(imap_email_infos: str) -> dict[str, str]:
    m_attrs = imap_message_attributes['ID'].match(imap_email_infos)
    # TODO: set how to report this upstream
    if not m_attrs:
        print(f'Error parsing {imap_email_infos} (parse_message_basic_attributes)')
    ret = {'ID': m_attrs[1]}
    for attr in ['FLAGS', 'SIZE', 'DATE']:
        c_attr = imap_message_attributes[attr].match(m_attrs[2])
        if c_attr:
            ret[attr] = c_attr[1]
    return ret


def folder_size(
    cnx: imaplib.IMAP4_SSL,
    folder_entry: bytes,
    returned_folder_attributes: dict[str, str | int],
        ) -> Exception | None:
    fs = 0
    nb = '0'
    # folder_entry.decode().split(' "/" ')
    # 2 element tuple
    imap_folder_match = imap_folder_re.match(str(folder_entry, 'utf-8'))
    if not imap_folder_match:
        return Exception(f"IMAP folder {folder_entry} does not match regexp (folder_size)")
    folder_items = imap_folder_match.group(1).split()
    # str(folder_entry, 'utf-8').split(' "/" ') same as folder_entry.decode().split(' "/" ')
    folder_flags = eval(','.join(folder_entry.decode().split(' "/" ')[0].replace('\\','').split(' ')).replace('(','("').replace(',','","').replace(')','",)'))
    s1 = set(folder_flags)
    special_folder = s1.intersection(special_folder_flags)
    # Folder name only
    mbx = '"' + ' '.join(map(lambda x: x.strip('"'), folder_items[1:])) + '"'
    # Folder is not selectable or is tagged with special meaning
    if len(special_folder) > 0:
        return Exception(f"{mbx} IMAP folder not processed {special_folder} (folder_size)")
    unknown_folder_flags = s1.difference(known_folder_flags)
    # TODO: see how to report this better upstream
    if len(unknown_folder_flags) > 0:
        print(f"{mbx} IMAP folder got unknown flag(s) -> {unknown_folder_flags} (folder_size)")
    # Select the desired folder
    result, nb = cnx.select(mbx, readonly=1)
    if result != 'OK':
        return Exception(f"{mbx} IMAP folder select returned {result} (folder_size)")
    # TODO: do some meaningful computation with flags
    # flags = cnx.response('FLAGS')
    # RECENT response element does not seem to be supported (anymore?)
    # recents = cnx.response('RECENT')
    unread_emails = 0
    # No need to further call IMAP server API
    if int(nb[0]) == 0:
        returned_folder_attributes.update({
            'name': folder_real_name(mbx.strip('"')),
            'messages': 0,
            'unread': 0,
            'size': 0,
            })
        return None
    # and/or verify that int(nb[0]) == len(msg[0].split())
    # Go through all the messages in the selected folder
    typ, msgs = cnx.search(None, 'ALL')
    if typ != 'OK':
        return Exception(f"{mbx} IMAP folder search returned bad status {typ} (and {msgs}) (folder_size)")
    m = [int(x) for x in msgs[0].split()]
    if not m:
        return Exception(f"{mbx} IMAP folder search returned empty list {msgs} (folder_size)")
    # Find the first and last messages
    # requires the list of IDs to be sorted
    m.sort()
    msgset = f"{m[0]}:{m[-1]}"
    # Add FLAGS to previously returned messages attributes
    # Same as FAST. See https://www.rfc-editor.org/rfc/rfc3501#section-6.4.5
    # result, msizes = cnx.fetch(msgset, "(FLAGS INTERNALDATE RFC822.SIZE)")
    result, msizes = cnx.fetch(msgset, "FAST")
    # TODO: potentially add further email message details
    # result, msizes = cnx.fetch(msgset, "(FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (From To Cc Bcc Subject Date Message-ID Priority X-Priority References Newsgroups In-Reply-To Content-Type Reply-To)])")
    if result != 'OK':
        return Exception(f"IMAP messages sizes returned {result} (folder_size)")
    # TODO: see how to report this better upstream
    if len(msizes) != int(nb[0]):
        print(f"{mbx} IMAP folder got unknown flag(s) -> {unknown_folder_flags} (folder_size)")
    messages_infos = []
    for msg in map(
        lambda x: parse_message_basic_attributes(str(x, 'utf-8')),
        msizes
    ):
        msg_size = int(msg['SIZE'])
        msg_date = None
        try:
            msg_date = datetime.strptime(
                msg['DATE'],
                '%d-%b-%Y %H:%M:%S %z'
                )
        except ValueError as e:
            # TODO: see hoe to report this better upstream
            print(f"IMAP message date decoding error: {msg[1]} {e} (folder_size)")
        messages_infos.append(
            {
                'id': msg.get('ID', 0),
                'size': msg_size,
                'date': msg_date,
                'flags': msg.get('FLAGS', '').split(),
                'folder': mbx,
            })
        if 'Seen' not in msg.get('FLAGS', ''):
            unread_emails += 1
        fs += msg_size
    returned_folder_attributes.update({
        'name': folder_real_name(mbx.strip('"')),
        'messages': int(nb[0]),
        'unread': unread_emails,
        'size': fs,
        'infos': messages_infos,
        })
    return None


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
    nunread_total = 0
    size_total = 0

    imap_folders = []
    messages_infos = []
    for folder in folders:
        folder_infos = dict()
        ex = folder_size(cnx, folder, folder_infos)
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
            nmessages_total += folder_infos['messages']
            size_total += folder_infos['size']
            nunread_total += folder_infos['unread']
            messages_infos.extend(folder_infos.get('infos', []))
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
    sdata = np.array(list(map(lambda x: x.get("size"), messages_infos)))
    ddata = np.array(list(map(lambda x: x.get("date"), messages_infos)))
    print(f"\nMessage sizes: [{sdata.min()} - {sdata.max()}]")
    print(f"\nMessage dates: [{ddata.min()} - {ddata.max()}]")
    over95percent = int(sdata.mean() + 2 * sdata.std())
    print(f"\nMessages over {human_readable_size(over95percent)} (upper 95% quartile):\n")
    to_save = 0
    big_messages = sorted(list(filter(lambda x: x.get("size", 0) > over95percent, messages_infos)), key=lambda x: x.get("size"))
    biggest = []
    for msg in big_messages:
        msg_from, msg_to, msg_subject = message_subject_from_to(cnx, msg)
        biggest.append([msg.get("id"), human_readable_size(msg.get("size")), (100.0 * msg.get("size")) / (1024 * quota_used), msg.get("date"), folder_real_name(msg.get("folder").strip('"')), msg_from, msg_subject])
        to_save += msg.get("size")
    print(tabulate.tabulate(biggest, headers=["ID", "Size", "%", "Date", "Folder", "From", "Subject"], floatfmt=".2f"))
    print(f"\nYou can save {human_readable_size(to_save)} ({((100*to_save)/(1024*quota_used)):.2f}%) by cleaning up the {len(big_messages)} biggest messages\n")
    # Close the connection
    cnx.logout()
