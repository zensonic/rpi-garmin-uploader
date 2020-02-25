# rpi-garmin-uploader
Import workouts from garmin devices to garmin connect using an rpi

Meet dependencies and then run

sudo nohup ./rpi-garmin-uploader.py --config rpi-garmin-uploader.json &

It will turn into background and wait for garmin device being inserted 
into rpi by usb cable, mount, sync, umount and upload new activites
to garmin_connect
