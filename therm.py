import os,select,subprocess,time
from random import randint
from datetime import datetime
import RPi.GPIO as GPIO
GPIO.cleanup()
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)


def brokerTimeCheck():
	#Check to see if broker has updated the reported time in the last 5 min
	#Assumes reported_time will be a string of format HH:MM on 24hr clock
	reportedHour=int(data['reported_time'].split(':')[0])
	reportedMin= int(data['reported_time'].split(':')[1])
	nowMin = int(datetime.now().strftime('%M'))
	nowHour = int(datetime.now().strftime('%H'))
	if nowHour == reportedHour:		#same hour
		timediff=nowMin-reportedMin
	elif (nowHour-1)%24==reportedHour:	#adjacent hours
		timediff=nowMin+60-reportedMin
	else:
		timediff= 60*((nowHour-reportedHour)%24) +nowMin-reportedMin #long times
	if timediff >= 5:
		print('Disconnected for %d min.'%timediff)
		return 0
	else:
		return 1

def clean():
	GPIO.cleanup()
	if os.path.exists(backup_filename):
		subprocess.Popen(['rm', backup_filename])
	print('GPIO settings and files have been reset')

def decode(aLine):
	#Removes the timestamp and cryptographic salt
	topic=aLine.split()[0]
	message=aLine.split(';')[-1]
	return topic+' '+message

def defaultValues():
	if os.path.exists(backup_filename):
		newDict=txt2dict()
		for key,val in newDict.items():
			update(key,str2int(val))
	else:	#no file found:
		for key,val in defaults.items():
			update(key,val)
	print(single_line)
	return 1

def encode(message):
	#Adds a timestamp & salt to the payload for cryptographic security
	salt=str(randint(0,999999999999))
	timestamp=datetime.now().strftime('%H:%M:%S')
	payload=timestamp+';'+salt+';'+message
	#print(payload)
	return payload

def executeSubscription():
	#Executes a terminal command and yields line-by-line output as it comes in
	cmd='mosquitto_sub -v -h %s -p 8883 -t  %s --cafile %s --cert %s --key %s'%(data['brokerIP'],subscribe_topic,cafile,clientcert,clientkey)
	process=subprocess.Popen(cmd.split(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)
	pollobj=select.poll()
	pollobj.register(process.stdout,select.POLLIN)
	starttime=time.time()
	while 1:
		pollresult=pollobj.poll(0)
		if pollresult:
			stdout_line=process.stdout.readline()
			if not stdout_line:	#Empty line
				pass #do nothing
			else:				#Something on line
				#print(stdout_line)
				#print('upper yield')
				starttime=time.time()
				yield stdout_line
		if time.time()-starttime>1*60:#wait for 1 min
			starttime=time.time()
			#print('lower yield')
			yield 'Time limit reached'
	#return_code = process.wait()
	#if return_code:
	#	raise subprocess.CalledProcessError(return_code,cmd)



def fireProtocol():
	#Smoke was detected. Safely shutdown and send warning.
	hvac('off')
	GPIO.cleanup()
	if os.path.exists(backup_filename):
		subprocess.Popen(['rm', backup_filename])
	print('GPIO settings and files have been reset')
	raise SystemExit

def getIP(hostname):
	#hostname='homebase'
	#Get the IP address of MQTT broker, given its hostname
	command='ping -c 1 -W 1 '+hostname
	ipproc=subprocess.Popen(command.split(),stdout=subprocess.PIPE)   
	out, err= ipproc.communicate()
	print(out)
	packetLossPercent=out.split('% packet loss')[0][-3:]
	if packetLossPercent=='100' or hostname not in out:
		print('IP address of broker not found.')
		print(double_line)
		return 0
	else:
		ip=out.split(hostname)[1].split('(')[1].split(')')[0]
		if '192.168.'!=ip[:8]: #Check if the ip found was local
			print(double_line)
			return 0
		else:
			print('MQTT Broker found at '+ip)
			print(double_line)
			return ip

def getTemp():
	#Use gpio-connected thermometer to get temperature
	return 74 #for now

def getTime():
	return datetime.now().strftime('%H:%M') 

def hvac(setting):
	#Uses GPIO pins to act as a thermostat
	#Turns on/off the relay attached to control pin
	#	setting:	"on" or "off"
	pins=[]
	if data['preference']=='cool':
		pins.append(pin_locations['compressor'])
		pins.append(pin_locations['fan'])
	else:	#data['preference']=='heat'
		pins.append(pin_locations['heater'])
		pins.append(pin_locations['fan'])
	for pin in pins:
		if setting=='on':
			GPIO.setup(pin,GPIO.OUT) #Hacky, but required on the hardware side #For now
			print('%s set to "on"'%pin)
		else: #setting=='off'
			GPIO.setup(pin,GPIO.IN) #Hacky, but required on the hardware side #For now
			print('%s set to "off"'%pin)
	return 1

def int2str(val):
	if isinstance(val,int):
		return str(val)
	else:
		return val

def laxTimeCheck(laxtimes):
	#Checks the time to see if lax mode should be on
	now=float(datetime.now().strftime('%H%M'))
	start=float(laxtimes.split(',')[0])
	stop=float(laxtimes.split(',')[1])
	if start>stop:
		print('Error: Invalid start time before stop time')
		raise ValueError('Error: start time before stop time in lax_times')
	elif now>start and now<stop:
		return 1
	else:
		return 0

def pub(subtopic,prepayload):
	#Publishes a given message to a given MQTT subtopic
	topic=subscribe_topic[:-1]+subtopic
	payload=encode(prepayload)
	cmdlist=['mosquitto_pub','-h',data['brokerIP'],'-p','8883','-t',topic,'-m',payload,'--cafile',cafile,'--cert',clientcert,'--key',clientkey,'-r']
	pubproc=subprocess.Popen(cmdlist,stdout=subprocess.PIPE)
	out, err= pubproc.communicate()
	return out
	
def pubDefaultSettings():
	#Publish default settings to mqtt server
	for key,val in defaults.items():
		pub(key,int2str(val))
	print(single_line)
	return 1
	
def runLogic():
	### Safety Check ###
	if data['smoke_detect']=='smoke' or data['messages']=='quit':
		fireProtocol()

	### Indirectly check if broker is still connected ###
	if data['brokerIP']==0 or brokerTimeCheck()==0: #Broker isn't connected or 5min passed. New Temp Needed.
		update('messages','Broker not connected. Failsafe operations engaged.')
		print(single_line)
		update('smoke_detect',smokeDetect())
		update('current_temp',getTemp())
		### No-broker Heating and Cooling ###
		if data['preference']=='cool':
			if (data['current_temp']>data['ideal_temp'] and data['status']=='off'):
				hvac('on')	#turn on air conditioner
				update('status','on')
			elif data['current_temp']<=data['ideal_temp'] and data['status']=='on':
				hvac('off')	#turn off airconditioner
				update('status','off')
		elif data['preference']=='heat':
			if data['current_temp']<data['ideal_temp'] and data['status']=='off':
				hvac('on')	#turn on heater
				update('status','on')
			elif data['current_temp']>=data['ideal_temp'] and data['status']=='on':
				hvac('off')	#turn off heater
				update('status','off')
	
	else: ### NORMAL OPERATIONS ###
		
		### Power-Saving Mode ###
		if data['lax_ability']:
			today=str(datetime.today().weekday())
			validtime= today in data['lax_days'] and laxTimeCheck(data['lax_times'])
			#print(validtime)
			if validtime and data['lax_status']=='off':
				#Time window is valid, turn on lax
				orig_ideal_temp=data['ideal_temp']
				pub('lax_status','on')
				pub('ideal_temp',str(data['lax_temp']))
				pub('messages','Power-saving mode activated.')
			elif validtime and data['lax_status']=='on' and data['lax_temp'] != data['ideal_temp']:
				#Lax temp was updated after turning on lax
				pub('ideal_temp',str(data['lax_temp']))
			elif not validtime and data['lax_status']=='on':
				#Time window is no longer valid, set to original
				pub('lax_status','off')
				pub('ideal_temp',str(orig_ideal_temp))
				pub('messages','Power-saving mode deactivated.')
		elif data['lax_ability']==0 and data['lax_status']=='on':
			#Lax ability was manually turned off, set to original
			pub('lax_status','off')
			pub('ideal_temp',str(orig_ideal_temp))
			pub('messages','Power-saving mode deactivated.')
		
		### Normal Heating and Cooling ###
		if data['preference']=='cool':
			if data['current_temp']>data['ideal_temp'] and data['status']=='off':
				hvac('on')	#turn on air conditioner
				pub('status','on')
			elif data['current_temp']<=data['ideal_temp'] and data['status']=='on':
				hvac('off')	#turn off airconditioner
				pub('status','off')
		elif data['preference']=='heat':
			if data['current_temp']<data['ideal_temp'] and data['status']=='off':
				hvac('on')	#turn on heater
				pub('status','on')
			elif data['current_temp']>=data['ideal_temp'] and data['status']=='on':
				hvac('off')	#turn off heater
				pub('status','off')
		else:
			text=r'Error: "preference" invalid. Set to default settings.'
			print(text)
			defaultValues()
	
	return 1

def smokeDetect():
	#Use GPIO-connected smoke detector
	smokeSignal=0.1 #for now
	if smokeSignal>=0.5:	#for now
		return 'smoke'
	else:
		return 'clear'

def str2int(val):
	if val.isdigit():
		return int(val)
	else:
		return val

def txt2dict():
	newDict={}
	with open(backup_filename,'r') as fn:
		lines=list(fn)
		#print(lines)
	for line in lines:
		key=line.split(delimiter)[0]
		val=line.split(delimiter)[1].replace('\n','')
		newDict[key]=val
	update('messages','Defaults taken from %s'%backup_filename)
	return newDict

def update(key,val):
	global data
	data[key]=val
	print('%s = %s'%(key,int2str(val)))
	return 1

def waitForConnection():
	update('brokerIP',getIP(hostname))
	if data['brokerIP']==0: #in the event of no broker connection
		update('messages','Failure to connect to broker. Defaults used.')
		tick=0
		while data['brokerIP']==0:
			defaultValues()
			runLogic()
			time.sleep(60)
			tick=tick+1
			update('messages','Total time waiting for connection: %d min'%tick)
			update('brokerIP',getIP(hostname))
	return

def writeBackupFile():
	with open(backup_filename,'w') as fn:
		text=''
		for key,val in data.items():
			if key in defaults:
				text=text+key+delimiter+int2str(val)+'\n'
		fn.write(text)
		#print('Modified file')
	return 1

### General Information ###
'''
---TOPIC STRUCTURE ---
thermostat/#        For all thermostat messages
    current_temp    Current temperature as measured (degrees F)
    ideal_temp      Ideal temperture to set room at (degrees F)
	lax_ability		Ability to run low-power mode yes=1 no=0
	lax_days		String of days of the week when lax mode should be active
						Monday=0 ... Sunday=6
	lax_status		Low-energy status on=1 off=0
	lax_temp		Temperature setting of lax mode
	lax_times		Times (on lax_days) when lax mode should be active
	messages        Other messages from thermostat
	reported_time	Time as reported by the broker, updated every 5min
						Used for connection checking
	preference      Specificies mode, "heat" or "cool"
	status          Specifies if actively running, "on" or "off"


--- PAYLOAD FORMAT ---
Includes timestamp and salt for cryptographic security
Timestamp;Salt;Message
YYYY-MM-DD,HH:MM:SS;930482034849;Message goes here

--- WIRE DEFINITIONS ---
	C: Common Ground (absent by default for me)
	G: Fan
	Y: Compressor
	W: Heat
	R: Electrical Hot 24VAC
Take picture of thermostat wiring beforehand to avoid possible confusion later.
In mine, 4 wires available; no C found. 5th wire found disconnected in wall.
I connect 5th wire as C to both HVAC & ground/regularly closed side of all relays.
I connect R to regularly open side of all relays.
G, Y, & W each get connected to the center pin of their own relays.
pinLocations are defined in a way to control each of these relays.
pinLocations are gpio on the rpi and are NOT directly connected to HVAC wires. 
'''

### INPUTS / GLOBAL VARIABLES ###
backup_filename='/home/pi/data.txt'
delimiter=';'
cafile='/etc/mosquitto/ca_certificates/ca.crt'
clientcert='/etc/mosquitto/certs/thermostat.crt'
clientkey='/etc/mosquitto/certs/thermostat.key'
hostname='homebase'
subscribe_topic='thermostat/#'
reset=1
data={} #Initialization of empty dictionary to store data
pin_locations={'fan':37,'compressor':37,'heater':37} #For now
single_line='-'*50
double_line='='*50
defaults={ #These data defaults are only used if no backup file exists
		'current_temp':74,
		'ideal_temp':74,
		'preference':'cool',
		'status':'off',
		'lax_ability':0,
		'lax_status':'off',
		'lax_days':'0,1,2,3,4',	#Monday=0 ... Sunday=6
		'lax_times':'2040,2345',
		'lax_temp':80,
		'messages':'Hard-coded default settings used.',
		'smoke_detect':'clear'
		}

### Main Execution ###
if __name__=='__main__':
	update('reported_time',getTime())
	waitForConnection() #Continues when successfully connected to broker
	pub('reported_time',getTime())
	pub('smoke_detect',smokeDetect())
	pub('current_temp',str(getTemp()))

	if reset:	#Reset the broker
		pubDefaultSettings()
		defaultValues()
	
	#cmd='mosquitto_sub -v -h %s -p 8883 -t  %s --cafile %s --cert %s --key %s'%(data['brokerIP'],subscribe_topic,cafile,clientcert,clientkey)
	print('Subscribing to topic \"%s\"'%subscribe_topic)
	print(double_line)
	for line in executeSubscription():
		#print(line)
		if 'reported_time' in data:	#Avoids data['reported_time'] not existing
			if brokerTimeCheck()==0: #Executes if no connection for 5min
				print('Connnection appears to be lost.')
				waitForConnection()
				update('reported_time',getTime())
				pub('reported_time',getTime())
		if line=='Time limit reached':	#Reports time every idle minute
			print('Preparing to report time...')
			pub('reported_time',getTime())
			pub('smoke_detect',smokeDetect())
			pub('current_temp',str(getTemp()))
			print(single_line)
		else:	#Execute normally
			modline=decode(line.replace('\n','').replace('\r',''))
			print(modline)
			mtopic=modline.split()[0].replace('thermostat/','')
			mmsg=str2int(' '.join(modline.split()[1:]))
			update(mtopic,mmsg)	#Save info from payload
				
			essentials=['current_temp','ideal_temp','preference','status','lax_ability','lax_status','lax_days','lax_times','lax_temp','reported_time']
			if all(key in data for key in essentials): #Check required data is ready
				#print('-'*30)
				runLogic()
		writeBackupFile()

