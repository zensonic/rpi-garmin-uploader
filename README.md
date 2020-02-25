# rpi-garmin-uploader
Import workouts from garmin devices to garmin connect using an rpi

Meet dependencies and then run

sudo nohup ./rpi-garmin-uploader.py --config rpi-garmin-uploader.json &

It will turn into background and wait for garmin device being inserted 
into rpi by usb cable, mount, sync, umount and upload new activites
to garmin_connect

Example from logfile when attaching an Garmin Edge 500 to the USB cable going into the raspberry pi

2020-02-25 16:17:02,607 [INFO] We got usb bind event. Starting mount
2020-02-25 16:17:05,477 [INFO]
2020-02-25 16:17:05,533 [INFO] Found scsi device matching 'Garmin'
2020-02-25 16:17:12,519 [INFO] Mount /dev/sdb onto /mnt
2020-02-25 16:17:15,903 [INFO] sync ok
2020-02-25 16:17:15,909 [INFO] sending incremental file list
2020-02-25 16:17:15,913 [INFO] ./
2020-02-25 16:17:15,918 [INFO] 2020-02-25-07-20-37.fit
2020-02-25 16:17:15,922 [INFO] 2020-02-25-16-16-52.fit
2020-02-25 16:17:15,927 [INFO]
2020-02-25 16:17:15,931 [INFO] sent 55,706 bytes  received 57 bytes  37,175.33 bytes/sec
2020-02-25 16:17:15,936 [INFO] total size is 1,486,144  speedup is 26.65
2020-02-25 16:17:18,781 [INFO] Umounted /mnt
2020-02-25 16:17:22,240 [WARNING] File '/tmp/import_activities.csv' extension '.csv' is not valid. Skipping file...
2020-02-25 16:17:22,242 [INFO] List file '/tmp/import_activities.csv' will be processed...
2020-02-25 16:17:22,249 [INFO] Try to login on GarminConnect...
2020-02-25 16:17:27,658 [INFO] Logged in as Thomas S. Iversen
2020-02-25 16:17:28,500 [INFO] Uploaded activity 4589938608 : 2020-02-25-16-16-52.fit
2020-02-25 16:17:29,992 [INFO] Activity already uploaded 4588430747 : 2020-02-25-07-20-37.fit
2020-02-25 16:17:29,994 [INFO] All done.
2020-02-25 16:17:30,404 [INFO] Uploaded files to garmin connect
