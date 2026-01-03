from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017")
db = client.chatapp
messages = db.messages
users = db.users
