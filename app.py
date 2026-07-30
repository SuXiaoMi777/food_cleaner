from flask import Flask, request, abort

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import *

#======python的函數庫==========
import tempfile, os
import datetime
import openai
import time
import traceback
import base64
import requests
#======python的函數庫==========

app = Flask(__name__)
static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
# Channel Access Token
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
# Channel Secret
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))
# OPENAI API Key初始化設定
openai.api_key = os.getenv('OPENAI_API_KEY')



def GPT_response(text):
    # 接收回應 (改用 ChatCompletion 寫法)
    response = openai.ChatCompletion.create(
        model="gpt-5-nano", # 填入你想要使用的模型名稱
        messages=[
            {"role": "system", "content":"你是一個營養師，你的任務是用有限的食材讓使用者吃到美味健康的一餐"},
            {"role": "user", "content": text}
        ],
        # temperature=0.5, 
        # max_completion_tokens=500
    )
    print(response)
    
    # 重組回應 (ChatCompletion 的回傳 JSON 結構與以往不同，需改為 ['message']['content'])
    answer = response['choices'][0]['message']['content']
    return answer

# 監聽圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    try:
        user_id = event.source.user_id # 取得使用者 ID
        loading_url = "https://api.line.me/v2/bot/chat/loading/start"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('CHANNEL_ACCESS_TOKEN')}"
        }
        data = {
            "chatId": user_id,
            "loadingSeconds": 20 # 動畫顯示秒數 (最高 20)
        }
        # 送出請求，使用者的 LINE 畫面會立刻出現「...」動畫
        requests.post(loading_url, headers=headers, json=data)

        # 1. 向 LINE 伺服器請求下載使用者傳送的圖片
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b""
        for chunk in message_content.iter_content():
            image_data += chunk
            
        # 2. 將二進位圖片資料轉換為 Base64 字串 (OpenAI 接收的格式)
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        # 3. 將圖片與 Prompt 結合，送給 OpenAI (使用支援視覺的 gpt-4o-mini)
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "你是一個專業的廚師。請辨識這張圖片中有哪些食材，並用這些食材幫我構想 2 ~ 3 道簡單的食譜，並且列出菜名與簡單的步驟。"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "low" # 使用 low 模式能大幅節省 Token 花費
                            }
                        }
                    ]
                }
            ],
            max_tokens=800,
            temperature=0.5
        )
        
        # 4. 擷取 AI 的回覆並傳送給使用者
        recipe_answer = response['choices'][0]['message']['content']
        print(f"AI 食譜回應：\n{recipe_answer}")
        
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(text=recipe_answer)
        )

    except Exception as e:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage('抱歉，在處理圖片時發生了一點問題，請稍後再試！')
        )


# 監聽所有來自 /callback 的 Post Request
@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']
    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# 處理訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    try:
        user_id = event.source.user_id # 取得使用者 ID
        loading_url = "https://api.line.me/v2/bot/chat/loading/start"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('CHANNEL_ACCESS_TOKEN')}"
        }
        data = {
            "chatId": user_id,
            "loadingSeconds": 20 # 動畫顯示秒數 (最高 20)
        }
        # 送出請求，使用者的 LINE 畫面會立刻出現「...」動畫
        requests.post(loading_url, headers=headers, json=data)

        GPT_answer = GPT_response(msg)
        print(f'AI 回應:{GPT_answer}')

        if not GPT_answer or GPT_answer.strip() == "":
            GPT_answer = "抱歉，我剛剛腦筋一片空白，可以請你再輸入一次嗎？"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(GPT_answer))
    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage('此服務目前不通...請稍後再試!'))
        

@handler.add(PostbackEvent)
def handle_message(event):
    print(event.postback.data)


@handler.add(MemberJoinedEvent)
def welcome(event):
    uid = event.joined.members[0].user_id
    gid = event.source.group_id
    profile = line_bot_api.get_group_member_profile(gid, uid)
    name = profile.display_name
    message = TextSendMessage(text=f'{name}歡迎加入')
    line_bot_api.reply_message(event.reply_token, message)
        
        
import os
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
