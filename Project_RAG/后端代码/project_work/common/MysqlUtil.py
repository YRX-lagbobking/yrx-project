import os
from dotenv import load_dotenv
import pymysql
load_dotenv()

def get_mysql():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DB"),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
def close_mysql(conn,cur):
    conn.close()
    cur.close()
