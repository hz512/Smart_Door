from datetime import datetime
import logging
import random
import base64
import boto3
import json
import time
import cv2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
passcodes = dynamodb.Table('passcodes')
visitors = dynamodb.Table('visitors')
msg_log = dynamodb.Table('msg_log')
s3 = boto3.client('s3')
BUCKET_NAME = "visitorfaces"
sns = boto3.client("sns", region_name="us-east-1")
OWNER_PHONE = "4344662282"
kinesis_client = boto3.client("kinesisvideo", region_name='us-east-1')
KVS_ARN = "arn:aws:kinesisvideo:us-east-1:866265759394:stream/kvs/1606892214747"


def lambda_handler(event, context):
    logger.info("LF1 started!")

    data = decode_data(event)
    have_face, fId = get_face(data)
    logger.info("detected face :{}".format(have_face))
    logger.info("faceId:{}".format(fId))
    known_visitor, name, phone_number = is_known_visitor(have_face, fId)
    print("known_visitor: " + str(known_visitor))
    
    if have_face:
        if known_visitor:
            if msg_sent_within_60s(phone_number):
                print("passcode sent to " + str(phone_number) + " within 60s")
                return
            add_known_visitor_photo(name, fId) 
            logger.info("known visitor, name: {}".format(name))
            passcode = generate_passcode()
            store_passcode(passcode, fId)
            logger.info("new passcode saved: {}".format(passcode))
            msg = msg_to_visitor(passcode)
        else:
            if msg_sent_within_60s(OWNER_PHONE):
                print("passcode sent to " + OWNER_PHONE + " within 60s")
                return
            logger.info("unknown visitor")
            phone_number = OWNER_PHONE
            img_url = get_unknown_visitor_photo()
            msg = msg_to_owner(img_url)

        sns.publish(
            PhoneNumber= "1" + str(phone_number),
            Message=msg
        )
        print("message sent to phone: {}".format(phone_number))

    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }


def decode_data(event):
    data_raw  = event['Records'][0]['kinesis']['data']
    data_str = base64.b64decode(data_raw).decode('ASCII')
    data = json.loads(data_str)
    logger.info(data)
    return data


def get_face(data):
    retrieved_faces = data["FaceSearchResponse"]
    # failed to detect face
    if len(retrieved_faces) ==0:
        return False, None
    matched_faces = retrieved_faces[0]["MatchedFaces"]
    # detect unknown face
    if len(matched_faces) == 0:
        return True, None
    # detect known face
    fId = matched_faces[0]["Face"]["FaceId"]
    return True, fId


def is_known_visitor(have_face,fId):
    if not have_face or fId is None:
        return False, None, None

    response = visitors.get_item(Key={"FaceId": fId})
    if "Item" not in response:
        return False, None, None
    return True, response["Item"]["name"], response["Item"]["phone_number"]


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


def generate_passcode():
    while True:
        passcode = ""
        for i in range(6):
            passcode += str(random.randint(0,9))
        response = passcodes.get_item(Key={"access_code": passcode})
        if "Item" not in response:
            break
    return passcode


def store_passcode(passcode, fId):
    ttl = int(time.time() + 300)
    passcodes.put_item(
        Item={
            "access_code": passcode,
            "faceId": fId,
            "ttl": ttl
        }
    )
    print("passcode was stored")


def msg_to_visitor(passcode):
    msg = "Hello, your passcode is " + str(passcode) + \
    ". The passcode is valid for 5 minutes. Use below URL to get into the door.\n" + \
            "http://door-visitors.s3-website-us-east-1.amazonaws.com"
    return msg


def append_photo_to_visitors(fId, photo_name, createdTimestamp):
    existing_photos = visitors.get_item(Key={"FaceId": fId})
    photos_list = json.loads(existing_photos['Item']['photos'])
    photos_list.append({
        "objectKey": photo_name, 
        "bucket": BUCKET_NAME, 
        "createdTimestamp": createdTimestamp
        })

    visitors.update_item(
        Key = {"FaceId": fId},
        UpdateExpression='SET photos = :val1',
        ExpressionAttributeValues={
            ':val1': json.dumps(photos_list)
        }
    )   


def add_known_visitor_photo(name, fId):
    response = kinesis_client.get_data_endpoint(StreamARN=KVS_ARN, APIName='GET_MEDIA')
    video_client = boto3.client(
        'kinesis-video-media', 
        endpoint_url=response['DataEndpoint'], 
        region_name='us-east-1'
    )
    response = video_client.get_media(
        StreamARN=KVS_ARN,
        StartSelector={'StartSelectorType': 'NOW'}
    )
    logger.info("add_known_visitor_photo setup complete")

    with open('/tmp/stream.mkv', 'wb') as f:
        stream = response['Payload']
        print("file opened successfully; begin writting...")
        f.write(stream.read(720*720))
        logger.info("mkv file written complete; now capture video")
        video_cap = cv2.VideoCapture('/tmp/stream.mkv')
        ret, frame = video_cap.read()
        cv2.imwrite('/tmp/frame.jpg', frame)
        createdTimestamp = str(datetime.now())
        logger.info("image retrieved")

        timestamp = str(time.time()).split(".")
        img_name = name + "_" + timestamp[0] + "_" + timestamp[1] + ".jpg"
        s3.upload_file(
            '/tmp/frame.jpg', # Filename
            BUCKET_NAME, # Bucket
            img_name, # Key
            ExtraArgs={'ACL':'public-read'}
        )
        logger.info("known visitor photo added to S3: visitorfaces")
        video_cap.release()

    append_photo_to_visitors(fId, img_name, createdTimestamp)
    logger.info("known visitor photo added to DynamoDB: visitors")



def get_unknown_visitor_photo():
    response = kinesis_client.get_data_endpoint(StreamARN=KVS_ARN, APIName='GET_MEDIA')
    video_client = boto3.client(
        'kinesis-video-media', 
        endpoint_url=response['DataEndpoint'], 
        region_name='us-east-1'
    )
    response = video_client.get_media(
        StreamARN=KVS_ARN,
        StartSelector={'StartSelectorType': 'NOW'}
    )
    logger.info("get_unknown_visitor_photo setup complete")

    with open('/tmp/stream.mkv', 'wb') as f:
        stream = response['Payload']
        print("file opened successfully; begin writting...")
        f.write(stream.read(720*720))
        logger.info("mkv file written complete; now capture video")
        video_cap = cv2.VideoCapture('/tmp/stream.mkv')
        ret, frame = video_cap.read()
        cv2.imwrite('/tmp/frame.jpg', frame)
        logger.info("image retrieved")

        img_name = "unknown_visitor.jpg"
        s3.upload_file(
            '/tmp/frame.jpg', # Filename
            BUCKET_NAME, # Bucket
            img_name, # Key
            ExtraArgs={'ACL':'public-read'}
        )
        logger.info("image uploaded to S3")
        video_cap.release()

    img_url = "https://" + BUCKET_NAME + ".s3.amazonaws.com/" + img_name
    return img_url


def msg_to_owner(img_url):
    msg = "There is an unknown visitor in front of the door. Use below " + \
                "URL to view visitor's face\n" + img_url
    return msg








