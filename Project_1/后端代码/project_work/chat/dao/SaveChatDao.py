from common import MysqlUtil
from chat.utils import CompressHistory


# 想要在这里实现历史记录
# 压缩功能，就是保存这个对话的时候
def save_chat(question, parent_id, user_id, answer):
    conn = MysqlUtil.get_mysql()
    cur = conn.cursor()
    '''
    查询操作不需要做事务管理，增删改需要做事务管理
    操作成功---commit 提交事务，执行当前操作
    操作失败---rollback 回滚事务，不执行当前操作
    '''
    # try:
        # # 转成int
        # parent_id = int(parent_id)
        # # 校验会话归属：parent_id 必须真实存在、是根节点、且属于当前用户；
        # # 前端传 0、传了别人的会话id或传了不存在的id时，一律按新会话处理
        # if parent_id != 0:
        #     cur.execute(
        #         "select history_id from history where history_id=%s and user_id=%s and parent_id=0",
        #         (parent_id, user_id)
        #     )
        #     if cur.fetchone() is None:
        #         parent_id = 0
        #
        # # 新会话：直接插入根节点，不参与压缩判断
        # # （parent_id=0 不是真实根节点，参与压缩会把全库所有用户的根一起算进去）
        # if parent_id == 0:
        #     cur.execute(
        #         "insert into history values(null,%s,%s,%s,%s,now())",
        #         (user_id, parent_id, question, answer)
        #     )
        #     cur.execute(
        #         "insert into history_for_chat values(null,%s,%s,%s,%s,now())",
        #         (user_id, parent_id, question, answer)
        #     )
        #     conn.commit()
        #     return cur.lastrowid
        #
        # # 统计当前用户当前会话的消息数（根节点+子节点），必须限定 username；
        # # order by history_id 保证 result[0] 一定是根节点
        # cur.execute(
        #     "select * from history_for_chat where user_id=%s and (history_id=%s or parent_id=%s) order by history_id",
        #     (user_id, parent_id, parent_id)
        # )
        # result = cur.fetchall()
        # # 不满10条：普通插入
        # if len(result) < 10:
        #     cur.execute(
        #         "insert into history values(null,%s,%s,%s,%s,now())",
        #         (user_id, parent_id, question, answer)
        #     )
        #     cur.execute(
        #         "insert into history_for_chat values(null,%s,%s,%s,%s,now())",
        #         (user_id, parent_id, question, answer)
        #     )
        #     conn.commit()
        #     return cur.lastrowid
        #
        # # 达到10条：历史记录压缩
        # # result[0] 是根节点，result[1:] 是待压缩的旧消息
        # document = result[1:]
        # compress_history = CompressHistory.compress_HISTORY(document)
        # # 删除根节点以外的旧消息，username，避免误删其他用户的会话
        # cur.execute(
        #     "DELETE FROM history_for_chat WHERE parent_id = %s AND user_id = %s",
        #     (parent_id, user_id)
        # )
        # # 插入压缩的会话
        # cur.execute(
        #     "insert into history_for_chat values(null,%s,%s,%s,%s,now())",
        #     (user_id, parent_id, '历史记录摘要', compress_history)
        # )
        # # 插入新会话
        # cur.execute(
        #     "insert into history_for_chat values(null,%s,%s,%s,%s,now())",
        #     (user_id, parent_id, question, answer)
        # )
        # cur.execute(
        #     "insert into history values(null,%s,%s,%s,%s,now())",
        #     (user_id, parent_id, question, answer)
        # )
        # # 执行成功
        # conn.commit()
        # print("保存记录成功")
        # return cur.lastrowid
    try:
        cur.execute(
        "insert into history values(null,%s,%s,%s,%s,%s,now())",(user_id, parent_id, question, answer,0)
        )
        conn.commit()
        print("保存记录成功")
    except Exception as e:
        # 执行失败
        conn.rollback()
        print(f"保存对话记录失败{e}")
        return 0
    finally:
        MysqlUtil.close_mysql(conn, cur)


# if __name__ == '__main__':
#         result=save_chat("你好2","1","草草","你好")
#         print(result)

'''
用户连续九次发送“你好2”，助手均以“你好”回应，无实质性问答内容，未涉及具体问题、需求或信息交换。

'''