def rrf(vector_result,bm25_result):
    score_dict = {}
    docs_dict = {}
    # 向量结果添加
    for index, item in enumerate(vector_result, start=1):
        score_dict[item.id] = score_dict.get(item.id, 0) + (1 / (60 + index))
        # score_dict.get(item.id,0)表示如果能去得到，就取出这个值再融合
        # 娶不到就直接+1/(60+index)
        docs_dict[item.id] = item  # 注意 这里添加的就是一个完整的item而不是item.page_contengt，因为后面我们返回的得是个Document对象

    # BM25结果添加
    for index, item in enumerate(bm25_result, start=1):
        score_dict[item.id] = score_dict.get(item.id, 0) + (1 / (60 + index))
        # score_dict.get(item.id,0)表示如果能去得到，就取出这个值再融合
        # 娶不到就直接+1/(60+index)
        docs_dict[item.id] = item
    # 对score_dict进行排序
    score_sort = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)
    """
        1.score_dict.items()表示将字典的键值对转化成一个一个元组，类似：[("D1", 0.5), ("D2", 0.9), ("D3", 0.3)]
        2.x[1]表示排序规则是用元组的索引[1]，也就是分数
        3.最后的结果是一个列表，里面包含元组：[('D2', 0.9), ('D1', 0.5), ('D3', 0.3)]   
        4.下面的[0]就取得排序后的id值
    """
    result = [docs_dict[item[0]] for item in score_sort]
    return result