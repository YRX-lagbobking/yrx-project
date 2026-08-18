from common import MysqlUtil,RedisUtil

def check_email(email):
    conn=MysqlUtil.get_mysql()
    cur =conn.cursor()
    sql="SELECT * FROM users WHERE email=%s"
    cur.execute(sql,[email])
    result=cur.fetchall()
    MysqlUtil.close_mysql(conn,cur)
    return result

#返回的结果有两种，如果找不到就是(),如果找到就是[]，所以可以通过这个来判断找到没
#[{'user_id': 1, 'user_name': '草草', 'email': '2779691651@qq.com', 'password': 123456, 'role_name': 'admin'}]

def check_code(email):
    conn=RedisUtil.get_redis_conn()
    code=conn.get(email)
    return code


def register(username:str, password:str, email:str, role:str):
    conn = MysqlUtil.get_mysql()
    cur =conn.cursor()
    try:
        sql='insert into users values (null,%s,%s,%s,%s)'
        cur.execute(sql,[username,password,email,role])
        conn.commit()
        return True
    except Exception as e:
        print(f"插入失败{e}")
        return False


def update_username(old_username, new_username):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        sql_1 = ('update users set username = %s where username = %s')
        sql_2=( 'update history set username = %s where username = %s')
        cur.execute(sql_1, [new_username, old_username])
        cur.execute(sql_2, [new_username, old_username])
        conn.commit()
        return True
    except Exception as e:
        print(f"数据库更新失败{e}")
        return False
#UPDATE user SET username = '新名字' WHERE username = '旧名字';
#UPDATE history SET username = '新名字' WHERE username = '旧名字';
def update_password(username, password):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        sql = ('update users set password = %s where username = %s')
        cur.execute(sql, [password, username])
        conn.commit()
        return True
    except Exception as e:
        print(f"数据库更新失败{e}")
        return False


def get_user_list():
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        sql = 'select * from users'
        cur.execute(sql)
        result = cur.fetchall()
        return result
    except Exception as e:
        print(f"数据库查询失败{e}")
        return False
# [{'user_id': 1, 'username': '草草', 'password': '$2b$12$DtrJwcTF6XBdekkL5i881eL2Yvh9BYyVRvcEpNk5VoOX3rp/DIUT2', 'email': '2779691651@qq.com', 'role': 'admin'},
# {'user_id': 2, 'username': '1', 'password': '$2b$12$58NQ.DCEdNxSzT1NVa7pA.wCuvaSEaM5.IAJch4TX3YYknBiG01Em', 'email': '1', 'role': 'user'},
# {'user_id': 3, 'username': '2', 'password': '$2b$12$NFrK2KljJkiZslR0QYZoXOGy4gXb15Hw/XiuYKhjQDNwK158dZfS6', 'email': '2', 'role': 'user'}]
