from common import MysqlUtil
#TRUNCATE TABLE 你的表名;
conn = MysqlUtil.get_mysql()
cur = conn.cursor()
sql = "TRUNCATE TABLE history"
cur.execute(sql, )
result = cur.fetchall()
MysqlUtil.close_mysql(conn,cur)