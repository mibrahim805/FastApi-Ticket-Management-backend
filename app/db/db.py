from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017")


db = client["mydatabase"]


user_collection = db["users"]
projects_collection = db["projects"]
ticket_collection = db["tickets"]
comments_collection = db["comments"]
notifications_collection = db["notifications"]
reset_tokens_collection = db["reset_tokens"]
statuses_collection = db["statuses"]
attachment_collection = db["attachments"]
ticket_activity_collection = db["ticket_activity"]
counters_collection = db["counters"]
project_members_collection = db["project_members"]
