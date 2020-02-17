#!/usr/bin/env python3 
# -*- coding: utf-8 -*-

# MIT License
# 
# Copyright (c) 2020 Thomas s. Iversen (zensonic@zensonic.dk)
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#  
#     The above copyright notice and this permission notice shall be included in all
#     copies or substantial portions of the Software.
# 
#     THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#     IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#     FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#     AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#     LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#     OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#     SOFTWARE.


# This little program will detect (pyudev), mount, rsync, umount and upload 
# Activity files from your garmin device to garmin connect. It has been tested
# using an garmin edge 500. 
#
# It utilizes the amazing https://github.com/La0/garmin-uploader for the upload
# part. It utilized pyudev for detecting the garmin and it uses sqlite3 for state

import pyudev
import time
import logging
import argparse
import subprocess
import os
import json
import re
import sys

import sqlite3
from sqlite3 import Error

parser = argparse.ArgumentParser(description='Import workouts from garmin devices to garmin connect using an rpi')
parser.add_argument('--config', help='config file')
args = parser.parse_args()


# Global state. We start in idle
state="sleep"

# Load json config if given
data={}
if args.config:
    with open(args.config) as json_file:
        data = json.load(json_file)

# expand variables or provide sane defaults
log_level           = data.get('log_level','INFO')
log_file            = data.get('log_file',os.path.basename(__file__) + ".log")
sleep_time          = int(data.get('sleep_time','1'))
scsi_search_string  = data.get('scsi_search_string','Garmin')
mount_point         = data.get('mount_point','/mnt')
activity_dest_dir   = data.get('activity_dest_dir','Activities')
activity_src_dir    = data.get('activity_src_dir','Garmin/Activities')
activity_sync_dir   = mount_point + "/" + activity_src_dir
garmin_user         = data.get('garmin_user')
garmin_password     = data.get('garmin_password')
sqlite3_db_name     = data.get('sqlite3_db_name','garminconnect.db')
tmp_import_file     = data.get('tmp_import_file','/tmp/import_activities.csv')

# Get a logger
logging.basicConfig(level=log_level,
    format='%(asctime)s [%(levelname)s] %(message)s',
    filename=log_file)

# pyudev startup
context = pyudev.Context()
monitor = pyudev.Monitor.from_netlink(context)
monitor.filter_by('usb')

# define and register pyudev callback
def udev_event(action, device):
    global state
    if(action == "bind" and state!= "mount"):
        logging.info("We got usb bind event. Starting mount")
        state="mount"

observer = pyudev.MonitorObserver(monitor, udev_event)
observer.start()


# Connect to the sqlite3 instance
def db_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Error as e:
        logging.error("Could not make sqlite3 connection: {}".format(e))
    return conn

# Inite tables if not exists
def db_init_tables(conn):
    sql="CREATE TABLE IF NOT EXISTS imported_activities (activity text PRIMARY KEY);"

    try:
        c = conn.cursor()
        c.execute(sql)
    except Error as e:
        logging.error("Could not ensure sqlite3 table was created: {}".format(e))

# Get all imported activities
def db_get_imported_activities(conn):
    sql="SELECT * from imported_activities order by activity"

    rows=[]
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
    except Error as e:
        loggint.error("Could not query db: {}".format(e))
    return rows

# Insert a single instance into the database
def db_insert_activity(conn,a):
    sql="INSERT OR IGNORE INTO imported_activities (activity) VALUES (?)"
    cur = conn.cursor()
    cur.execute(sql, a)
    conn.commit()
    return cur.lastrowid

# Mount the garmin storage
def mount(state):
    logging.info("")
    logging.debug(state)

    dev=None
    stream = os.popen('lsscsi')
    for d in stream.readlines():
        if(re.search(scsi_search_string,d)):
            dev=d.split()[-1]

    if dev:
        logging.info("Found scsi device matching '{}'".format(scsi_search_string))
        stream = os.popen('mount')
        already_mounted=False
        for l in stream.readlines():
            if(re.search(dev,l)):
                already_mounted=True
        if not already_mounted:
            stream = subprocess.Popen("mount " + dev + " " + mount_point,shell=True).wait()
            logging.info("Mount {} onto {}".format(dev,mount_point))
            if(stream == 0):
                # If we managed to mount, then sync                    
                state="sync"
        else:
            # If it is already mounted, sync
            state="sync"
    return state

# Sync from garmin to internal storage
def sync(state):
    logging.debug(state)
    if not os.path.isdir(activity_dest_dir):
        os.mkdir(activity_dest_dir)
    if not os.path.isdir(activity_sync_dir):
        logging.error(activity_sync_dir + " does not exist. Can't sync from it")
    else:
        p=subprocess.run("rsync -av " + activity_sync_dir + "/ " + activity_dest_dir + "/",shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT )
        if(p.returncode == 0):
            logging.info("sync ok") 
        else:
            logging.error("Could not sync")
        for l in p.stdout.splitlines():
            logging.info(l.decode())
    return "umount"

# Umount garmin after sync
def umount(state):
    logging.debug(state)
    stream = subprocess.Popen("umount " + mount_point,shell=True ).wait()
    if(stream == 0):
        logging.info("Umounted " + mount_point)
    else: 
        logging.error("Could not umount " + mount_point)
    return "upload"


# Calculate what activities to upload based on what we have 
# on file and what the database says we have successfully uploaded
# before

def to_import(on_disk,imported):
    d = { i[0] : 1 for i in imported }
    s = []
    for j in on_disk:
        if j not in d:
            s.append(j)
    return s

# Make a list of files to upload to gupload
# instaed of uploading everything we upload what we are missing
# to upload based on state in database
def create_import_file(list_to_import):
    filename=tmp_import_file

    f = open(filename,"w")
    f.write("filename,name,type\n")
    for i in list_to_import:
        f.write("{}/{},,uncategorized\n".format(activity_dest_dir,i))
    f.close()

    return filename

# Upload files to garmin connect. For files uploaded, register uploaded state 
# in database ... so they will never be uploaded again
def upload(state):
    logging.debug(state)
    entries_on_disk = os.listdir(activity_dest_dir)
    entries_already_imported=db_get_imported_activities(conn)

    list_to_import=to_import(entries_on_disk,entries_already_imported)

    if list_to_import:
        filename=create_import_file(list_to_import)
        p=subprocess.run("gupload " + filename + " -u " + garmin_user +" -p " + garmin_password, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        f=open(log_file,"a")
        for l in p.stdout.splitlines():
            ld=l.decode()
            f.write(ld + "\n")
            if(re.search("already uploaded",ld) or re.search("Upload",ld)):
                a=ld.split()[-1]
                db_insert_activity(conn,[a])
        f.close()

        if(p.returncode == 0):
            logging.info("Uploaded files to garmin connect")
        else: 
            logging.error("Could not upload files to garmin connect")
    else:
        logging.info("No new activities to import")
    return "sleep"

# When idle, sleep
def idle():
    logging.debug("sleep {} seconds".format(sleep_time))
    time.sleep(sleep_time)

# switch functions based on state
def main():
    global state
    conn=db_connection(sqlite3_db_name)
    db_init_tables(conn)
    while True:
        if state == 'mount':
            state=mount(state)
        elif state == 'sync':
            state=sync(state)
        elif state == 'umount':
            state=umount(state)
        elif state == 'upload':
            state=upload(state)
        else:            
            idle() 

if __name__ == '__main__':
    main()
