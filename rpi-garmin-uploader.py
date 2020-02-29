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

# Global device to process
device_to_process=None
block_devices_to_process=set()
usb_devices_to_process={}

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
            'sleep_time':         '5',
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

def locate_block_device(dev):
    for child_dev in dev.children:
        print(child_dev)


# define and register pyudev callback
def udev_event(action, device):
    global state
    global usb_devices_to_process
    if(action == "bind"):
        logging.info("We got usb bind event.")
        logging.debug(device)
        logging.info("Adding usb device path {} to set of devices to process".format(device.device_path))
        usb_devices_to_process[device.device_path]=0


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

    gdi=None
    mount_point=vars['mount_point']

    if device_to_process:
        logging.info("Going to mount '{}'".format(device_to_process))
        stream = os.popen('mount')
        already_mounted=False
        for l in stream.readlines():
            if(re.search(device_to_process,l)):
                already_mounted=True
        if not already_mounted:
            stream = subprocess.Popen("mount " + device_to_process + " " + mount_point,shell=True).wait()
            logging.info("Mount {} onto {}".format(device_to_process,mount_point))
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
        logging.info("Going to sync {} to {}".format(activity_sync_dir,activity_dest_dir)) 
        p=subprocess.run("rsync -av " + activity_sync_dir + "/ " + activity_dest_dir + "/",shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT )
        if(p.returncode == 0):
            logging.info("sync ok") 
        else:
            logging.error("Could not sync")
        for l in p.stdout.splitlines():
            logging.info(l.decode())
    return "upload"

# Umount garmin after sync
def umount(state):
    logging.debug(state)
    mount_point=vars['mount_point']
    stream = subprocess.Popen("umount " + mount_point,shell=True ).wait()
    if(stream == 0):
        logging.info("Umounted " + mount_point)
    else: 
        logging.error("Could not umount " + mount_point)
    return "sleep"


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
        logging.info("Going to upload {} activities to garmin connect".format(len(list_to_import)))
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
        logging.info("No new activities to upload")
    return "umount"

# When idle, sleep
def idle():
    global block_devices_to_process
    global device_to_process
    global state

    # If no work to do, then sleep
    device_to_process=None

    # If there is no work, sleep
    if len(block_devices_to_process)==0 and len(usb_devices_to_process)==0:
        sleep_time=vars['sleep_time']
        logging.debug("sleep {} seconds".format(sleep_time))
        time.sleep(int(sleep_time))

    for usb_path in usb_devices_to_process.copy().keys():
        logging.debug(usb_path)
        device=pyudev.Devices.from_path(context, usb_path)
        for dev in device.children:
            logging.debug(dev)
            if(re.search("/block/",dev.device_path)):
                blockdevice=dev.device_path.split("/")[-1]
                if blockdevice:
                    logging.info("Adding block device {} to set of devices to process".format("/dev/" + blockdevice))
                    block_devices_to_process.add("/dev/" + blockdevice)
                    usb_devices_to_process.pop(device,None)

    if(len(block_devices_to_process)>0):
        device_to_process=block_devices_to_process.pop()
        logging.debug("Processing {}".format(device_to_process))
        state="mount"

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
            garmin_user=vars['garmin_user'] 
            if garmin_user:
                logging.info("Found valid garmin user {}. Going to sync".format(garmin_user))
                state=sync(state,gdi)
            else:
                logging.info("Can not find valid garmin user. Not going to sync. Going to umount")
                state="umount"                  
        elif state == 'upload':
            state=upload(state,conn,gdi)
        elif state == 'umount':
            state=umount(state)
        else:            
            idle() 

if __name__ == '__main__':
    main()
