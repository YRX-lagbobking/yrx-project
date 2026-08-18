from chat.dao import HistoryDao
from chat.utils import CompressHistory


def query_history_menu(user_id):
    result = HistoryDao.query_history_menu(user_id)
    # 包装结果
    data = []
    # 结果长这样：是列表套字典格式
    # [{'history_id': 1, 'question': '你好', 'username': '草草', 'parent_id': 0, 'answer': '你好呀！', 'create_time': datetime.datetime(2026, 8, 4, 10, 9, 47)},
    for item in result:
        data.append(
            {"title": item["question"], "time": item["createtime"].strftime("%Y-%m-%d %H:%M:%S"),
             "historyId": item["history_id"]}
        )

    return {
        "code": 200,
        "msg": "查询成功",
        "data": data
    }

def conversation_log(history_id):
    result = HistoryDao.conversation_log(history_id)
    data = []
    for item in result:
        data.append(
            {"role": "user", "content": item["question"]}
        )
        data.append(
            {"role": "assistant", "content": item["answer"]}
        )
    return {
        "code": 200,
        "msg": "查询成功",
        "data": data
    }
from chat.entity.DeleteHistoryEntity import DeleteHistoryClass
def delete_history(deleteHistory:DeleteHistoryClass):
    history_id=deleteHistory.history_id
    user_id=deleteHistory.user_id
    result=HistoryDao.delete_history(history_id,user_id)
    if result:
        return {
            "code": 200,
            "msg": "删除成功",
            "data": None
        }
    return {
        "code": 500,
        "msg": "删除失败",
        "data": None
    }

def search_messages(user_id:str, keyword:str):
    result = HistoryDao.search_messages(user_id, keyword)
    if result:
        return {
            "code": 200,
            "msg": "查询成功",
            "data": result
        }
    return {
        "code": 500,
        "msg": "没有找到相关记录",
        "data": None
    }


def history_compress(history_id):
    result = HistoryDao.search_compress_chat(history_id)
    lens=len(result)
    if lens >=10:
        print("需要进行历史记录压缩")
        #就需要进行压缩，
        compress_chat=result[:5]
        # 压缩对话
        compress_history = CompressHistory.compress_HISTORY(compress_chat)
        #将压缩对话的内容上传到压缩会话表
        HistoryDao.insert_compress_history(compress_history, history_id)
        # 对每个需要进行压缩的对话的iscompress进行修改
        for item in compress_chat:
            HistoryDao.update_iscompress(item["history_id"], 1)
        #组装历史记录--------------------------------------
        history_1=HistoryDao.history_make(history_id)
        history_2=HistoryDao.get_compress_history(history_id)
        data = []
        for item in history_2:
            data.append(
                {"role": "system", "content": item["answer"]}
            )
        for item in history_1:
            data.append(
                {"role": "user", "content": item["question"]}
            )
            data.append(
                {"role": "assistant", "content": item["answer"]}
            )
        return data
    else:
        print("不用进行压缩")
        #不用压缩直接进行提取历史记录
        history_1=HistoryDao.history_make(history_id)
        print("history_1:",history_1)
        data=[]
        for item in history_1:
            data.append(
                {"role": "user", "content": item["question"]}
            )
            data.append(
                {"role": "assistant", "content": item["answer"]}
            )
        return data

