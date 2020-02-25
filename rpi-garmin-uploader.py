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
from xml.dom import minidom

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

def default_vars():
    vars= {
            'log_level':          'INFO',
            'log_file':           os.path.basename(__file__) + ".log",
            'sleep_time':         '1',
            'scsi_search_string': 'Garmin',
            'mount_point':        '/mnt',
            'activity_dest_dir':  'Activities',
            'activity_src_dir' :  'Garmin/Activities',
            'sqlite3_db_name':    'garminconnect.db',
            'tmp_import_file':    '/tmp/import_activities.csv'
            }
    return vars

def update_vars(vars,jsonvars):
    for k in jsonvars.keys():
        vars[k]=jsonvars[k]

vars=default_vars()

global_vars=data.get('global')
if global_vars:
    update_vars(vars,global_vars)

# Get a logger
logging.basicConfig(level=vars['log_level'],
    format='%(asctime)s [%(levelname)s] %(message)s',
    filename=vars['log_file'])

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
    sql="CREATE TABLE IF NOT EXISTS imported_activities (activity text PRIMARY KEY, user text not null);"

    try:
        c = conn.cursor()
        c.execute(sql)
    except Error as e:
        logging.error("Could not ensure sqlite3 table was created: {}".format(e))

# Get all imported activities
def db_get_imported_activities(conn,user):
    sql="SELECT * from imported_activities where user=? order by activity"

    rows=[]
    try:
        cur = conn.cursor()
        cur.execute(sql, (user,))
        rows = cur.fetchall()
    except Error as e:
        logging.error("Could not query db: {}".format(e))
    return rows

# Insert a single instance into the database
def db_insert_activity(conn,a,user):
    sql="INSERT OR IGNORE INTO imported_activities (activity,user) VALUES (?,?)"
    cur = conn.cursor()
    a.append(user)
    cur.execute(sql, a)
    conn.commit()
    return cur.lastrowid

def get_garmin_device_id(mount_point):
    f=mount_point + "/Garmin/GarminDevice.xml"
    gdi=None
    if  os.path.isfile(f):
        doc = minidom.parse(f)
        gid = doc.getElementsByTagName('Id')
        if gid:
            for e in gid:
                e.normalize()
                if(e.firstChild.data):
                        gdi=e.firstChild.data
    return gdi

# Mount the garmin storage
def mount(state):
    logging.info("")
    logging.debug(state)

    dev=None
    gdi=None
    stream = os.popen('lsscsi')
    scsi_search_string=vars['scsi_search_string']
    mount_point=vars['mount_point']
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
                gdi=get_garmin_device_id(mount_point)
                # If we managed to mount, then sync                    
                state="get_gdi_specific_vars"


        else:
            # If it is already mounted, sync
            gdi=get_garmin_device_id(mount_point)
            state="get_gdi_specific_vars"
    return (state,gdi)

def get_gdi_specific_vars(state,gdi):
    dev_data=data.get('devices')
    if dev_data:
        specific=dev_data.get(gdi)
        if (specific):
            update_vars(vars,specific)
            print(vars)
    return "sync"
    

# Sync from garmin to internal storage
def sync(state,gdi):
    logging.debug(state)

    # if gdi exists, we might have device specific overrides

    activity_dest_dir=vars['activity_dest_dir']
    activity_sync_dir   = vars['mount_point'] + "/" + vars['activity_src_dir']
    
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
    mount_point=vars['mount_point']
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
    filename=vars['tmp_import_file']

    f = open(filename,"w")
    f.write("filename,name,type\n")
    for i in list_to_import:
        f.write("{}/{},,{}\n".format(vars['activity_dest_dir'],i,vars['activity_type']))
    f.close()

    return filename

# Upload files to garmin connect. For files uploaded, register uploaded state 
# in database ... so they will never be uploaded again
def upload(state,conn,gdi):
    logging.debug(state)
    entries_on_disk = os.listdir(vars['activity_dest_dir'])
    garmin_user=vars['garmin_user']
    entries_already_imported=db_get_imported_activities(conn,garmin_user)

    list_to_import=to_import(entries_on_disk,entries_already_imported)

    if list_to_import:
        filename=create_import_file(list_to_import)
        p=subprocess.run("gupload " + filename + " -u " + garmin_user +" -p " + vars['garmin_password'], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        f=open(vars['log_file'],"a")
        for l in p.stdout.splitlines():
            ld=l.decode()
            f.write(ld + "\n")
            if(re.search("already uploaded",ld) or re.search("Upload",ld)):
                a=ld.split()[-1]
                db_insert_activity(conn,[a],garmin_user)
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
    sleep_time=vars['sleep_time']
    logging.debug("sleep {} seconds".format(sleep_time))
    time.sleep(int(sleep_time))

# switch functions based on state
def main():
    global state
    conn=db_connection(vars['sqlite3_db_name'])
    db_init_tables(conn)
    while True:
        if state == 'mount':
            (state,gdi)=mount(state)
        elif state == 'get_gdi_specific_vars':
            (state)=get_gdi_specific_vars(state,gdi)
        elif state == 'sync':
            state=sync(state,gdi)
        elif state == 'umount':
            state=umount(state)
        elif state == 'upload':
            state=upload(state,conn,gdi)
        else:            
            idle() 

if __name__ == '__main__':
    main()
