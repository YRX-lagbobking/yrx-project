from logging import exception

from common import MysqlUtil

def query_history_menu(user_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    sql = "select * from history where user_id=%s and parent_id = 0"
    cur.execute(sql, [user_id])
    result = cur.fetchall()
    MysqlUtil.close_mysql(conn,cur)
    return result

'''
#结果长这样：是列表套字典格式
    #[{'history_id': 1, 'question': '你好', 'username': '草草', 'parent_id': 0, 'answer': '你好呀！', 'create_time': datetime.datetime(2026, 8, 4, 10, 9, 47)},
'''

def conversation_log(history_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    sql = "select * from history where history_id = %s or parent_id= %s "
    cur.execute(sql, [history_id, history_id])
    result = cur.fetchall()
    MysqlUtil.close_mysql(conn, cur)
    return result

def delete_history(historyId,user_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    '''
    查询操作不需要做事务管理，增删改需要做事务管理
    操作成功---commit 提交事务，执行当前操作
    操作失败---rollback 回滚十五，不执行当前操作
    '''
    try:
        sql = "delete from history where (history_id = %s or parent_id= %s )and user_id = %s ORDER BY history_id ASC;"
        cur.execute(sql,[historyId,historyId,user_id])
        #执行成功
        conn.commit()
        print("删除成功")
        MysqlUtil.close_mysql(conn, cur)
        return True
    except Exception as e:
        #执行失败
        conn.rollback()
        MysqlUtil.close_mysql(conn, cur)
        print(f"删除失败{e}")
        return  False


def search_messages(user_id:str, keyword:str):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        '''
                在这里进行SQL查询，根据keyword进行模糊查询，如果它的parent_id=0就返回它自己的history_id，
                 如果不等于0，就返回它的parent_id----就是为了找到根节点。
                 DISTINCT 保证我们只
        '''
        sql_root = """
                   SELECT DISTINCT CASE
                                       WHEN parent_id = 0 THEN history_id
                                       ELSE parent_id
                                       END AS root_id
                   FROM history
                   WHERE user_id = %s
                     AND (question LIKE CONCAT('%%', %s, '%%')
                       OR answer LIKE CONCAT('%%', %s, '%%')) \
                   """
        cur.execute(sql_root, (user_id, keyword, keyword))
        root_rows = cur.fetchall()
        print("root_rows:", root_rows)
        # 长这样：root_rows: [{'root_id': 1}, {'root_id': 16}]
        if not root_rows:
            return False

        # 字典取值
        root_ids = [row['root_id'] for row in root_rows]
        print("root_ids:", root_ids)
        data = []
        for item in root_ids:
            sql_1 = "select * from history where history_id=%s "
            cur.execute(sql_1, [item])
            result_1 = cur.fetchall()
            '''
            #结果长这样：是列表套字典格式
    #[{'history_id': 1, 'question': '你好', 'username': '草草', 'parent_id': 0, 'answer': '你好呀！', 'createtime': datetime.datetime(2026, 8, 4, 10, 9, 47)},
            '''

            '''
            将结果组装成需要的格式，也就是历史列表格式
            '''
            for item in result_1:
                data.append(
                    {"title": item["question"], "time": item["createtime"].strftime("%Y-%m-%d %H:%M:%S"),
                     "historyId": item["history_id"]}
                )

        return data

    finally:
        MysqlUtil.close_mysql(conn, cur)

def search_compress_chat(history_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        sql = "select * from history where parent_id=%s and iscompress=0 and parent_id!=0"#没有检索到根节点
        cur.execute(sql, [history_id])
        result = cur.fetchall()
        return result
    except Exception as e:
        return None
    finally:
        MysqlUtil.close_mysql(conn, cur)
def update_iscompress(history_id, iscompress):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        sql = "update history set iscompress=%s where history_id=%s"
        cur.execute(sql, [iscompress, history_id])
        conn.commit()
        return True
    except Exception as e:
        print("update_iscompress错误", e)
        return False
    finally:
        MysqlUtil.close_mysql(conn, cur)


def insert_compress_history(compress_history, history_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        sql = "insert into history_for_chat values (null,%s, %s, %s, now())"
        cur.execute(sql, [history_id,"压缩文本内容",compress_history ])
        conn.commit()
        return True
    except Exception as e:
        print("insert_compress_history错误", e)
        return False
    finally:
        MysqlUtil.close_mysql(conn, cur)

def history_make(history_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    try:
        #检索所有没被压缩的文本（带根节点）
        sql = "select * from history where(parent_id=%s or history_id=%s) and iscompress=0 ORDER BY history_id ASC"
        cur.execute(sql, [history_id, history_id])
        result = cur.fetchall()
        return result
    except Exception as e:
        print("history_make错误", e)
        return None
    finally:
        MysqlUtil.close_mysql(conn, cur)
#结果：
#[{'history_id': 1, 'user_id': 1, 'parent_id': 0, 'question': '1', 'answer': '1', 'iscompress': 0, 'createtime': datetime.datetime(2026, 8, 17, 20, 0, 53)}]


def get_compress_history(history_id):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    #获取history_for_chat表中parent_id为history_id的所有压缩文本的值
    sql = "select answer from history_for_chat where parent_id=%s"
    cur.execute(sql, [history_id])
    result = cur.fetchall()
    MysqlUtil.close_mysql(conn, cur)
    return result
#结果：
#[{'answer': '11'}, {'answer': '22'}, {'answer': '33'}, {'answer': '44'}]
# if __name__ == '__main__':
#     result=history_make(1)
#     print(result)