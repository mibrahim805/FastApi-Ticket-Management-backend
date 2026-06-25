from pymongo import MongoClient
client = MongoClient("mongodb://localhost:27017")



db = client["mydatabase"]




user_collection = db["users"]
boards_collection = db["boards"]
ticket_collection = db["tickets"]
comments_collection = db["comments"]
notifications_collection = db["notifications"]
reset_tokens_collection = db["reset_tokens"]



