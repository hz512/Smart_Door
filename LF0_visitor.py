import json
import boto3
import time
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
passcodes = dynamodb.Table('passcodes')
visitors = dynamodb.Table('visitors')
msg_log = dynamodb.Table('msg_log')
s3 = boto3.client('s3')
FACES_BUCKET = "visitorfaces"

def lambda_handler(event, context):
    passcode = retrieve_passcode(event)
    if passcode is None:
        return failure_response("Sorry, we failed to retrieve your passcode. Please try again.")
    print("passcode retrieved: " + str(passcode))
    fId = find_visitor(passcode)
    print("faceId retrieved: " + str(fId))
    if fId is None:
        return failure_response("Your passcode is incorrect or expired. Contact the owner to get a new passcode.")
    name, face_url = get_visitor_info(fId)
    print("name: " + name + "; face_url: " + face_url)
    if name is None or face_url is None:
        return failure_response("Sorry, we failed to retrieve your name and face image. Please contact the owner.")
    return success_response(name, face_url)


def retrieve_passcode(event):
    if "messages" not in event:
        logger.error("failed to retrieve passcode")
        return None
    messages = event["messages"]
    if len(messages) < 1:
        logger.error("no message")
        return None
    return messages[0]["unconstructed"]["passcode"]


def failure_response(txt):
    body = {
        "messages":[
            {
                "type":"failure responce",
                "unconstructed":{
                    "valid": False,
                    "vaistor_info": None,
                    "text": txt,
                }
            }
        ]
    }
    return {
        'statusCode': 200,
        'body': body
    }


def find_visitor(passcode):
    response = passcodes.get_item(Key={"access_code": passcode})
    if "Item" not in response:
        return None
    fId = response['Item']['faceId']
    ttl = response['Item']['ttl']
    if time.time() > ttl:
        return None 
    passcodes.delete_item(key={"access_code": passcode})
    return fId


def get_visitor_info(fId):
    response = visitors.get_item(Key={"FaceId": fId})
    if "Item" not in response:
        return None, None
    visitor = response["Item"]
    name = visitor["name"]
    photos = json.loads(visitor["photos"])
    if len(photos) < 1:
        return None, None
    img_name = photos[0]["objectKey"]
    face_url = "https://visitorfaces.s3.amazonaws.com/" + img_name
    return name, face_url


def success_response(name, face_url):
    visitor = {
        "name": name,
        "photo": face_url
    }
    body = {
        "messages":[
            {
                "type":"success response",
                "unconstructed":{
                    "valid": True,
                    "visitor_info": visitor,
                }
            }
        ]
    }
    return {
        'statusCode': 200,
        'body': body
    }




