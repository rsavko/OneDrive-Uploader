# -*- coding: utf-8 -*-
__author__ = 'Roman Savko'

import os
import requests
import copy
import json
import webbrowser
import time
from threading import Timer
from urllib import urlencode

cfg = {
    "client_id": "<Your Client ID>",
    "secret": "<Your Client Secret>",
    "token_type": "bearer",
    "token": None,
    "refresh_token": None,
    "expires_in": None
}

URL = "https://api.onedrive.com/v1.0"
redirect_url = "https://login.live.com/oauth20_desktop.srf"
exclude = ["Google Photos Backup", "Photos Library.photoslibrary"] #folders to exclude

requests.packages.urllib3.disable_warnings()


def check_token_valid():
    if cfg["token"] is None:
        return False
    resp = requests.get(URL + "/drive", headers=get_headers())
    return resp.status_code == requests.codes.ok


def authenticate():
    payload = {
        "client_id": cfg["client_id"],
        "scope": "wl.signin wl.offline_access onedrive.readwrite",
        "response_type": "code",
        "redirect_uri": redirect_url
    }
    url = "https://login.live.com/oauth20_authorize.srf?" + urlencode(payload)
    print("A new browser tab will be opened. Please copy \"code\" value from URL.")
    webbrowser.open_new_tab(url)
    code = raw_input("Code: ")
    print("Redeem the code for access tokens...")
    payload = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_url,
        "secret": cfg["secret"],
        "code": code,
        "grant_type": "authorization_code"
    }
    resp = requests.post("https://login.live.com/oauth20_token.srf", data=urlencode(payload),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    init_config(resp.json()) if resp.status_code == requests.codes.ok else resp.raise_for_status()


def init_config(json):
    cfg["token_type"] = json["token_type"]
    cfg["token"] = json["access_token"]
    cfg["refresh_token"] = json["refresh_token"]
    cfg["expires_in"] = json["expires_in"]
    interval = cfg["expires_in"] - (cfg["expires_in"] / 10)
    timer = Timer(interval, prolong_token)
    timer.setDaemon(True)
    timer.start()
    print("Config initialized.")


def prolong_token():
    payload = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_url,
        "secret": cfg["secret"],
        "refresh_token": cfg["refresh_token"],
        "grant_type": "refresh_token"
    }
    print("Trying to prolong token...")
    resp = requests.post("https://login.live.com/oauth20_token.srf", data=urlencode(payload),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})

    init_config(resp.json()) if resp.status_code == requests.codes.ok else resp.raise_for_status()
    print("Token prolonged.")


def get_headers():
    return {"Authorization": cfg["token_type"] + " " + cfg["token"]}


def resolve_drive_id():
    resp = requests.get(URL + "/drive", headers=get_headers())
    drive_id = resp.json()['id'] if resp.status_code == requests.codes.ok else resp.raise_for_status()
    print("Resolved Drive ID is " + drive_id)
    return drive_id


def resolve_root_item_id(name, drive_id):
    response = requests.get(URL + "/drives/{}/root/children".format(drive_id), headers=get_headers())
    if response.status_code == requests.codes.ok:
        items = response.json()['value']
        for i in items:
            if i['name'] == name:
                item_id = i['id']
                print("Resolved Item ID for \"" + name + "\" is " + item_id)
                return item_id
        raise RuntimeError("Root item '{}' not found in Drive.".format(name))
    else:
        response.raise_for_status()


def process_directory(dir, root_item_id):
    for item in os.listdir(dir):
        if not item.startswith(".") and item != os.path.basename(__file__) and item not in exclude:
            full_path = os.path.join(os.path.realpath(dir), item)
            upload(full_path, root_item_id)


def upload(item_path, parent_id):
    filename = os.path.basename(item_path)
    filename = unicode(filename, "utf-8")
    isdir = os.path.isdir(item_path)
    if isdir:
        _create_dir(filename, item_path, parent_id)
    else:
        _upload_file(filename, item_path, parent_id)


def _create_dir(filename, item_path, parent_id):
    payload = {"name": filename, "folder": {}, "@name.conflictBehavior": "rename"}
    url = URL + "/drive/items/{}/children/".format(parent_id)
    heads = copy.deepcopy(get_headers())
    heads['Content-Type'] = 'application/json'
    print(u"Creating folder \"{}\"...".format(filename))
    response = requests.post(url, data=json.dumps(payload), headers=heads)

    if response.status_code == requests.codes.created:
        parent_id = response.json()['id']
        if filename != response.json()['name']:
            print(u"Folder \"{}\" created as \"{}\".".format(filename, response.json()['name']))
        process_directory(item_path, parent_id)
    else:
        print(u"Failed to create directory \"{}\"".format(filename))
        response.raise_for_status()


def _upload_file(filename, item_path, parent_id):
    threshold = 10 * 1024 * 1024  # 10Mb
    file_size = os.path.getsize(item_path)
    heads = copy.deepcopy(get_headers())
    time.sleep(0.2)
    print(u"\nUploading file {}...".format(filename))

    if file_size > threshold:
        url = URL + u"/drive/items/{}:/{}:/upload.createSession".format(parent_id, filename)
        heads['Content-Type'] = 'application/json'
        response = requests.post(url, headers=heads)
        if response.status_code == requests.codes.ok:
            upload_url = response.json()['uploadUrl']
            bytes_uploaded = 0
            with open(item_path, 'rb') as f:
                while True:
                    chunk = f.read(threshold)
                    if chunk:
                        heads['Content-length'] = len(chunk)
                        heads['Content-Range'] = "bytes {}-{}/{}".format(bytes_uploaded,
                                                                         bytes_uploaded + len(chunk) - 1, file_size)
                        response = _try_upload(upload_url, chunk, heads)
                        if response.status_code == requests.codes.accepted:
                            bytes_uploaded += len(chunk)
                            print("Uploaded bytes: {} out of {}".format(bytes_uploaded, file_size))
                        elif response.status_code == requests.codes.not_found:
                            print("Problem with upload. Starting the entire upload over...")
                            _upload_file(filename, item_path, parent_id)
                    else:
                        break
            print("File \"" + filename + "\" was uploaded.")
        else:
            response.raise_for_status()
    else:
        url = URL + u"/drive/items/{}:/{}:/content".format(parent_id, filename)
        #heads['Content-Type'] = 'application/octet-stream'
        #heads['Content-length'] = file_size

        with open(item_path, 'rb') as f:
            _try_upload(url, f, heads)
        print("File uploaded.")


def _try_upload(url, chunk, heads):
    response = None
    for n in range(0, 11):
        if n > 0:
            print("Upload re-try #{}...".format(n))
        try:
            print("Uploading...".format(n))
            response = requests.put(url, data=chunk, headers=heads)
            print("Done.")
            if response.status_code not in [requests.codes.ok, requests.codes.accepted, requests.codes.created]:
                print("Response status: {} ({}).".format(response.status_code, response.reason))
                raise IOError
            return response
        except IOError as e:
            print("Exception: " + str(e))
            time.sleep(2 ** n)
        finally:
            if response is not None:
                response.close()
    raise IOError("Filed to upload file")


if __name__ == '__main__':
    token_valid = check_token_valid()

    if not token_valid and cfg["client_id"] is not None and cfg["secret"] is not None:
        authenticate()
        token_valid = check_token_valid()

    if not token_valid:
        help_msg = "You can authorize against OneDrive using 'token' or using registered application with 'client ID' "
        help_msg += "and a client secret.\n\n"
        help_msg += "Enter 1 if you have authorization 'token' or\n"
        help_msg += "Enter 2 if you have 'client ID' and a 'client secret'\n"
        print(help_msg)
        answer = ""

        while answer not in ["1", "2"] or not token_valid:
            answer = raw_input("Please make a choice: ")
            if answer == "1":
                cfg["token"] = raw_input("Token: ")
            else:
                cfg["client_id"] = raw_input("Client ID: ")
                cfg["secret"] = raw_input("Client Secret: ")
                authenticate()
            token_valid = check_token_valid()

    drive_id = resolve_drive_id()
    root_item_id = resolve_root_item_id("Pictures", drive_id)
    process_directory(os.curdir, root_item_id)
