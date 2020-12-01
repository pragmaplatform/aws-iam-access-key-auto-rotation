'''
 Setup Steps:
   1.) Import CSV of account mapping to Dynamodb (bulk import via script)
   2.) Create S3 bucket
   3.) Add email templates to S3

 Script Steps:
   1.) Trigger off of event sent (SNS to Lambda trigger)
   2.) Parse received event
   3.) Query Dynamodb with AccountID to get Account Email
   4.) Collect email template from S3
   5.) Update email template based on event received
   6.) Send email via SES to Account Email with updated template

 IAM Permissions Needed:
   -Dynamodb read
   -S3 get object
   -Send email via SES

Sample Event to test with:
{
    "Records":
    [{
        "EventSource": "aws:sns",
        "EventVersion": "1.0",
        "EventSubscriptionArn": "arn:aws:sns:<REGION>:<ACCOUNT_ID>:SNSNotificationForSgIngressRules:<TOPIC_ID>",
        "Sns": {
            "Type": "Notification",
            "MessageId": "7eba7817-5b18-55f7-9b84-2fc2f35767b8",
            "TopicArn": "arn:aws:sns:<REGION>:<ACCOUNT_ID>:SNSNotificationForSgIngressRules",
            "Subject": "Config Rule - Wide Open SG Rule Detected",
            "Message": "Overly permissive All Ports Rule Detected!\n\nSecurity Group Id(s): ['<SG_ID>']\nAccount: <ACCOUNT_ID>\nRegion: <REGION>\n\n\nThis notification was generated by the Lambda function arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:EC2-Security-Group-Fix-All-Open-Ports-LambdaFunctionName",
            "Timestamp": "2020-09-11T16:30:07.409Z",
            "SignatureVersion": "1",
            "Signature": "MLay+YZuLNKe/7U8PfDVvPzDsI8FHm+2+So+9ain7XN5Rd/swgZpNRfycyj/lH4Od4fSj8IJ1vrbyTcZppzsD+vYzqW1DbihjC41fcvsemDP94Dpq1FgT/Qz+ZeunMp3czMwvQSI6DbQ2rdP9JuhG1AVqvIJVeUCNxfiDl8gmRzJs6hkwbNkDGWsGnRTnDKMzbSwEjR3BYK9Hahcu1LAVdGFe3yWi9WugQzr+YlHeQyE/UBv5W+YWdi47m7lOmYGGDe7hbZTuXc6IH6qt78D3Eo4oUzM+W9jqUxLrIFv4/dfmBLhEY6Wzt2K2/3nlPITqHdda0X1uY1X3A19PHQVGw==",
            "SigningCertUrl": "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-a86cb10b4e1f29c941702d737128f7b6.pem",
            "UnsubscribeUrl": "https://sns.us-west-2.amazonaws.com/?Action=Unsubscribe&SubscriptionArn=arn:aws:sns:<REGION>:<ACCOUNT_ID>:SNSNotificationForSgIngressRules:<TOPIC_ID>",
            "MessageAttributes": {}
        }
    }]
}
'''

import json
import re
import boto3
import os


def lambda_handler(event, context):

    event_details = parse_event(event) #returns: account_id,event_subject,event_message
    account_details = get_account_email(event_details[0]) #returns: account_id,accountname,accountemail
    email_template = get_email_template(event_details[1]) #returns: email raw template
    email_template_modified = update_email_template(email_template, account_details, event_details[1], event_details[2]) #returns: email template with account-specific details

    try:
        send_email(account_details[2], event_details[1], email_template_modified)
        response = {
            'statusCode': 200,
            'body': json.dumps('Email sent!')
        }
        print("[Logger] Email sent successfully!")
    except:
        response = {
            'statusCode': 200,
            'body': json.dumps('ERROR email not sent!')
        }
        print("[ERROR] Email NOT sent.")

    return response

def parse_event(event):
    """
    Function analyzes and parses received event.

    :param event: event that triggered lambda.
    :return list of account_id, event_subject, & event_message
    """
    match_custom_config_rules = ''
    match_aws_managed_config_rules = ''

    try:
        event_message = event['Records'][0]['Sns']['Message']
        event_subject = event['Records'][0]['Sns']['Subject']

        print('[Logger] Custom Config Rule Detected')

        regex_account_id = r'Account: [0-9]*'
        match_custom_config_rules = re.search(regex_account_id, event_message)

        #Parse out account id from message
        if match_custom_config_rules:
            account_id_msg = match_custom_config_rules.group()
            account_id = account_id_msg.split(' ')
            account_id = account_id[1]
            print('[Logger] Found Account ID '+ account_id)

        else:
          print('[Logger] Did not find Account ID')

    except:
        print('[Logger] AWS Managed Config Rule Detected')
        account_id = event['account']
        event_subject = event['detail-type']
        event_message = str(event)

    return(account_id,event_subject,event_message)

def get_account_email(account_id):
    """
    Function Queries Dynamodb with AccountID to find the Account Email.

    :param accountid: accountid parsed from the trigger event
    :return list of account details (account_id,accountname,accountemail)
    """
    dynamodb_client = boto3.client('dynamodb')

    dynamodb_table_name = os.environ["dynamodb_table_name"]

    response = dynamodb_client.get_item(TableName=dynamodb_table_name, Key={'uuid':{'S':str(account_id)}})

    if 'Item' not in response:
        print('[ERROR] AccountID '+account_id+ ' not found in Dynamodb mapping.')
    else:
        accountemail = response['Item']['accountemail']['S']
        accountname = response['Item']['accountname']['S']
        print('[Logger] AccountID '+account_id+ "'s email " + accountemail + " found in Dynamodb mapping.")

    return(account_id,accountname,accountemail)

def get_s3_object(bucket_name,filename):
    """
    :param bucket_name: String S3 Bucket Name
    :param filename: String S3 Bucket Key/File Name
    :return body: Contents of the S3 Object
    """
    # Resource initialized
    s3 = boto3.resource('s3')

    obj = s3.Object(
        bucket_name=bucket_name,
        key=filename
    )

    # Read S3 Bucket Object
    body = obj.get()['Body'].read()

    return body

def get_email_template(event_type):
    """
    Function analyzes and parses received event.

    :param event_type: event type from the parsed event to know which template to download.
    :return html object of the corresponding email template
    """
    bucket_key = ''

    if 'New AWS IAM Access Key Pair Created' in event_type:
        # 3  - IAM Key Rotation Rule - Detected
        bucket_key = 'IAM Auto Key Rotation Enforcement.html'

    else:
        print("Parser not found for event, need to update code to support it")
        bucket_key = ''

    print("[Logger] Downloaded email template for event type: '"+ event_type + "', file: ["+ bucket_key + "]")
    s3_bucket_name = os.environ["s3_bucket_name"]
    s3 = boto3.resource('s3')
    try:
        email_object = get_s3_object(s3_bucket_name, bucket_key)
        # Convert binary into string
        email_template_decoded = str(email_object, 'utf-8')
    except:
        print("[Logger] S3 Object could not be opened. Check environment variable. ")
        email_template_decoded = ''

    return email_template_decoded

def update_email_template(html_template, account_details, event_details_subject, event_details_message):
    """
    Function analyzes and parses received event.

    :param html_template: raw html email template
    :param account_details: list of event details (account_id, account_email, event_resource, etc)
    :param event_details_subject: event subject type
    "param event_details_message: raw event message
    :return updated html template with event-specific attributes
    """
    print("[Logger] Modifying raw email template with event-specific data")

    html_template_modified = html_template.replace("[insert-iolations-here]", event_details_message)

    return html_template_modified

def send_email(end_user_email, subject_text, html_email_body):
    """
    Function analyzes and parses received event.

    :param end_user_email: Account email of the end user in violation of the event detection
    :param subject_text: Subject to be used for email
    :param html_email_body: Email template with event & account-specific details
    :return status code if email was sent successfully
    """
    print("[Logger] Sending email to: " + end_user_email)

    ses_client = boto3.client('ses')

    admin_email_source = os.environ["admin_email_source"]

    response = ses_client.send_email(
        Source=admin_email_source,
        Destination={
            'ToAddresses': [
                end_user_email,
            ]
        },
        Message={
            'Subject': {
                'Data': subject_text
            },
            'Body': {
                'Text': {
                    'Data': html_email_body
                },
                'Html': {
                    'Data': html_email_body
                }
            }
        }
    )

    return response
