from passlib.context import CryptContext
#创建哈希对象
crypy_context= CryptContext(schemes=["bcrypt"],deprecated="auto")
#密码加密：明文--使用salt[盐]、随机生成的、它会自己帮我们记住-->密文
def hash_password(password:str)->str:
    return crypy_context.hash(password)

#密码比对：明文加密再和密文比对
def verify_password(plain_password:str,hashed_password:str)->bool:
    return crypy_context.verify(plain_password,hashed_password)

# #$2b$12$om/NDAYtYXUvfKCAYFap1eg0i5/zR62q2qef/4jAKhhycnPfsHy7i
# hash_password="$2b$12$om/NDAYtYXUvfKCAYFap1eg0i5/zR62q2qef/4jAKhhycnPfsHy7i"
# print (verify_password("1",hash_password))