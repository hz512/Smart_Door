from datetime import datetime
import logging
import random
import boto3
import json
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
passcodes = dynamodb.Table('passcodes')
visitors = dynamodb.Table('visitors')
msg_log = dynamodb.Table('msg_log')
s3 = boto3.client('s3')
FACES_BUCKET = "visitorfaces"
sns = boto3.client("sns", region_name="us-east-1")
OWNER_PHONE = "4344662282"
rekognition =boto3.client('rekognition')
COLLECTION_ID ='faceCollection'

UNKNOWN_VISITOR_IMG_URL = "https://visitorfaces.s3.amazonaws.com/unknown_visitor.jpg"


def lambda_handler(event, context):
    logger.info("LF0_owner starts")

    name, phone_number, img_name = get_visitor_info(event)
    if None in [name, phone_number, img_name]:
    	return failure_response("failed to retrieve visitor's info")
    
    known_img_name = save_img_as_known_to_s3(name, img_name)
    fId = add_faces_to_collection(known_img_name)
    save_record_to_visitors(fId, name, phone_number, known_img_name)
    passcode = save_record_to_passcodes(fId)
    if msg_sent_within_60s(phone_number):
    	print("passcode sent to " + str(phone_number) + " within 60s")
    	return
    sns.publish(
            PhoneNumber= "1" + str(phone_number),
            Message= msg_to_visitor(passcode)
    )
    print("message sent to visitor: " + str(phone_number))
    return success_response()


def get_visitor_info(event):
	if "messages" not in event:
		logger.error("failed to retrieve visitor's info")
		return None, None, None
	messages = event["messages"]
	if len(messages) < 1:
		logger.error("no message")
		return None, None, None

	name = messages[0]["unconstructed"]["name"]
	phone_number = messages[0]["unconstructed"]["phone_number"]
	img_name = "unknown_visitor.jpg"
	return name, phone_number, img_name


def failure_response(err):
    body = {
        "messages":[
            {
                "type":"failure_response",
                "unconstructed":{
                    "valid": False,
                    "text": err,
                    "time": str(datetime.now())
                }
            }
        ]
    }   
    return {
        'statusCode': 200,
        'body': body
    }


def msg_sent_within_60s(phone_number):
    response = msg_log.get_item(Key={"phone_number": phone_number})
    if "Item" not in response:
        msg_log.put_item(
            Item={
	            "phone_number": phone_number,
	            "last_msg": int(time.time())
            }
        )
        return False
    else:
        last_msg = response["Item"]["last_msg"]
        curr_time = int(time.time())
        if curr_time > last_msg + 63:
            msg_log.put_item(
                Item={
	                "phone_number": phone_number,
	                "last_msg": curr_time
                }
            )
            return False
        else:
            return True


def save_img_as_known_to_s3(name, unknown_img_name):
	known_img_name = name + ".jpg"
	logger.info("new visitor image is saved as {}".format(known_img_name))
	s3.download_file(FACES_BUCKET, unknown_img_name, '/tmp/visitor.jpg')
	response = s3.upload_file('/tmp/visitor.jpg', FACES_BUCKET, 
    	known_img_name, ExtraArgs={'ACL':'public-read'})
	print("visitor's photo is added to S3: visitorfaces")
	boto3.resource("s3").Object(FACES_BUCKET, unknown_img_name).delete()
	logger.info("unknown_visitor.jpg deleted from S3: visitorfaces")
	return known_img_name


def add_faces_to_collection(known_img_name):
	response = rekognition.index_faces(CollectionId=COLLECTION_ID,
                                Image={'S3Object':
                                {'Bucket':FACES_BUCKET,'Name':known_img_name}},
                                ExternalImageId=known_img_name,
                                MaxFaces=1,
                                QualityFilter="AUTO",
                                DetectionAttributes=['ALL'])
	fId = response['FaceRecords'][0]['Face']['FaceId']
	print("a new face image is added to Rekognition collection; face Id: " + str(fId))
	return fId


def save_record_to_visitors(fId, name, phone_number, known_img_name):
    photos = [
                {
                	"objectKey": known_img_name,
                 	"bucket": FACES_BUCKET,
                 	"createdTimeStamp": str(datetime.now()),
                }
    ]
    visitors.put_item(
        Item={
            "FaceId": fId,
            "name": name,
            "phone_number": phone_number,
            "photos": json.dumps(photos)
        }
    )


def generate_passcode():
    while True:
        passcode = ""
        for i in range(6):
            passcode += str(random.randint(0,9))
        response = passcodes.get_item(Key={"access_code": passcode})
        if "Item" not in response:
            break
    return passcode


def save_record_to_passcodes(fId):
    ttl = int(time.time() + 300)
    passcode = generate_passcode()
    passcodes.put_item(
        Item={
            "access_code": passcode,
            "faceId": fId,
            "ttl": ttl
        }
    )
    print("new passcode saved: {}".format(passcode))
    return passcode


def msg_to_visitor(passcode):
    msg = "Hello, your passcode is " + str(passcode) + \
    ". The passcode is valid for 5 minutes. Use below URL to get into the door.\n" + \
            "http://door-visitors.s3-website-us-east-1.amazonaws.com"
    return msg


def success_response():
    body = {
        "messages":[
            {
                "type":"success response",
                "unconstructed":{
                    "valid": True,
                    "text": "visitor has been authenticated",
                    "time": str(datetime.now())
                }
            }
        ]
    }
    return {
        'statusCode': 200,
        'body': body
    }

