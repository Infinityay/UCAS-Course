from bs4 import BeautifulSoup
import requests
import re
import base64
import ddddocr
import json
import os
import sys
import datetime
from .score_update_logger import MyLogger
from .util import detailException
from Crypto.Cipher import PKCS1_v1_5 as Cipher_pksc1_v1_5
from Crypto.PublicKey import RSA
from prettytable import PrettyTable

logger = MyLogger('ScoreUpdateMonitor')
class CourseInfoFetcher:
    pub_re = re.compile(r'var jsePubKey = \'(.*?)\'')
    error_re = re.compile(r'<div class="alert alert-error">(.*?)</div>',re.S)
    redirect_re = re.compile(r'2秒钟没有响应请点击<a href="(.*?)"><strong>这里', re.S)
    root_path = os.path.dirname(os.path.realpath(sys.argv[0]))
    userInfo_path = os.path.join(root_path,'config','userInfo.json')
    module_path = os.path.join(root_path,'module','sep.onnx')
    charsets_path = os.path.join(root_path,'module','charsets.json')
    with open(os.path.join(root_path,'config','config.json'),'r',encoding='utf-8') as f:
        config = json.load(f)
    headers = {
        'User-Agent': config['User-Agent'],
    }
    login_url = config['login_url']
    pic_url = config['pic_url']
    slogin_url = config['slogin_url']
    redirect_url = config['redirect_url']
    redirect_url_master = config['redirect_master_url']
    score_base_url = config['score_base_url']
    course_base_url = config['course_base_url']
    course_info_url = config['course_info_url']
    course_base_url_master = config['course_base_url_master']

    def __init__(self):
        # 使用自己训练的模型
        self.ocr = ddddocr.DdddOcr(show_ad=False,ocr=False,det=False,import_onnx_path=self.module_path,charsets_path=self.charsets_path)
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.keep_alive = True
        # 从config加载配置
        self.semester_start_date = self.config['semester_start_date']
        self.current_semester = self.config['current_semester']
        self.section_time_table = self.config['section_time_table']
        self.master_section_time_table = self.config['master_section_time_table']
        # 加载用户信息
        with open(self.userInfo_path,'r') as f:
            userInfo = json.load(f)
        self.username = userInfo['userName']
        self.password = userInfo['password']
    
    @staticmethod
    def encrypt(password, public_key):
        public_key = '-----BEGIN PUBLIC KEY-----\n'+ public_key + '\n-----END PUBLIC KEY-----'
        rsakey = RSA.importKey(public_key)
        cipher = Cipher_pksc1_v1_5.new(rsakey)
        cipher_text = base64.b64encode(cipher.encrypt(password.encode()))
        return cipher_text.decode()

    @detailException
    def __do_login(self):
        response = self.session.get(self.login_url)
        if response.status_code == 200:
            # 获取公钥
            pub_keys = self.pub_re.findall(response.text)
            if len(pub_keys) == 0:
                self.session.close()
                raise Exception('get public key fail')
            pub_key = pub_keys[0]
            # 获取验证码
            pic = self.session.get(self.pic_url)
            # 识别验证码
            if pic.status_code == 200:
                img_bytes = pic.content
                certCode = self.ocr.classification(img_bytes)
            else:
                self.session.close()
                raise Exception(f'get certCode error code: {pic.status_code}, {pic.text}')
            # 密码的加密
            password = self.encrypt(self.password, pub_key)
            # 登陆
            data = {
                'userName': self.username,
                'pwd': password,
                'certCode': certCode,
                'sb': 'sb'
            }
            response = self.session.post(self.slogin_url, data=data)
            if response.status_code == 200:
                fail = self.error_re.findall(response.text)
                if len(fail) != 0:
                    self.session.close()
                    raise Exception(fail[0])
            else:
                self.session.close()
                raise Exception(f'login error code: {response.status_code}, {response.text}')
        else:
            self.session.close()
            raise Exception(f'try to login but fail, error code: {response.status_code}, {response.text}')
    
    @detailException
    def __login(self,retry=3):
        count = 0
        while True:
            # 若登陆失败的原因是验证码错误，则重试
            try:
                self.__do_login()
                return
            except Exception as e:
                if str(e) == '验证码错误':
                    count += 1
                    if count < retry:
                        continue
                    else:
                        raise Exception('验证码错误次数过多')
                else:
                    raise e
    @detailException
    def get_master_course_name_and_time_link(self):
        print("\n=== 研究生课程信息 ===")
        course_count = 0
        event_list = []
        response = self.session.get(self.redirect_url_master)
        if response.status_code == 200:
            redirect_urls = self.redirect_re.findall(response.text)
            if len(redirect_urls) == 0:
                self.session.close()
                raise Exception(f'get redirect url fail, all response is {response.text}')
            redirect_url = redirect_urls[0]

            response = self.session.get(redirect_url)
            if response.status_code == 200:
                section_to_time = self.master_section_time_table
                print(section_to_time)

                soup = BeautifulSoup(response.text, 'html.parser')
                tbody = soup.find('tbody')

                if not tbody:
                    raise Exception('Cannot find tbody in the response')

                courses = []
                for tr in tbody.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) >= 2:
                        course_name = tds[1].text.strip()
                        course_time_link = tds[1].find('a')['href']
                        courses.append(course_time_link)

                print(f"课程总数: {len(courses)}")
                print(f"课程链接: {courses}")
                
                for course_link in courses:
                    try:
                        if not course_link.endswith('.json'):
                            course_link = course_link + '.json'
                            
                        response = self.session.get(course_link)
                        if response.status_code != 200:
                            print(f"获取课程信息失败，状态码: {response.status_code}")
                            continue
                            
                        course_info_json = response.json()
                        if "courseTimeList" not in course_info_json:
                            continue
                        
                        course_time_list = course_info_json["courseTimeList"]

                        for schedule in course_time_list:
                            try:
                                course_count += 1
                                print(f"\n第 {course_count} 节课:")
                                print(f"课程名称: {schedule['courseName']}")
                                week_binary = bin(int(schedule["courseWeek"]))[2:][::-1]
                                time_binary = bin(int(schedule["courseTime"]))[2:]
                                time_day = int(time_binary[:-12], 2)
                                time_section_binary = time_binary[-12:][::-1]
                                time_section_list = []
                                week_list = []
                                for idx, bit in enumerate(time_section_binary):
                                    if bit == '1':
                                        time_section_list.append(str(idx + 1))
                                for idx, bit in enumerate(week_binary):
                                    if bit == '1':
                                        week_list.append(idx + 1)

                                number2day = {1: "星期一", 2: "星期二", 3: "星期三", 4: "星期四", 5: "星期五", 6: "星期六", 7: "星期日"}
                                print(f"时间: {number2day[time_day]}")
                                print(f"节数: {time_section_list}")
                                print(f"周数: {week_list}")
                                print(f"地点: {schedule['coursePlace']}")
                                print("-------")

                                for week in week_list:
                                    event_list.append({
                                        "event_name": schedule["courseName"],
                                        "event_location": schedule["coursePlace"],
                                        "event_week": week,
                                        "event_day": time_day,
                                        "event_time": (
                                            section_to_time[time_section_list[0]][0],
                                            section_to_time[time_section_list[-1]][1]
                                        )
                                    })
                            except Exception as e:
                                print(f"处理课程时间段时出错: {str(e)}")
                                continue

                    except Exception as e:
                        print(f"处理课程时出错: {str(e)}")
                        continue

                
            else:
                raise Exception(f'Failed to retrieve redirected URL, status code: {response.status_code}')
        else:
            raise Exception(f'Failed to retrieve master URL, status code: {response.status_code}')
        # Read ics content from "course_schedule_tmp.ics"
        ics_content = ""
        with open("course_schedule_tmp.ics", "r") as f:
            ics_content = f.read()
        
        for event in event_list:
            ics_content += self.convert_event_to_ics_format(
                event["event_name"],
                event["event_location"],
                event["event_week"],
                event["event_day"],
                event["event_time"]
            )
        
        ics_content += "END:VCALENDAR\n"

        # save ics file
        with open("course_schedule.ics", "w", encoding='utf-8') as f:
            f.write(ics_content)

        self.print_schedule_table(event_list)
        
        print(f"\n研究生课程总数: {course_count} 节")



    @detailException
    def __get_course_data(self):
        print("\n=== 本科生课程信息 ===")
        course_count = 0
        event_list = []

        response = self.session.get(self.redirect_url)
        if response.status_code == 200:
            redirect_urls = self.redirect_re.findall(response.text)
            if len(redirect_urls) == 0:
                self.session.close()
                raise Exception(f'get redirect url fail,all response is {response.text}')
            redirect_url = redirect_urls[0]
            response = self.session.get(redirect_url)
            if response.status_code == 200:
                all_url = self.course_base_url + 'selectedCourse.json'
                response = self.session.get(all_url)
                selected_course_data = response.json()
                term_course_ids = []
                for course in selected_course_data["list"]:
                    if course["termName"] == self.current_semester:
                        term_course_ids.append(course["courseId"])
        
                
                for course_id in term_course_ids:
                    course_info_url = self.course_info_url + str(course_id) + ".json" 
                    response = self.session.get(course_info_url)
                    course_info_json = response.json()
                    course_time_list = course_info_json["courseTimeList"]

                    for schedule in course_time_list:
                        course_count += 1
                        print(f"\n第 {course_count} 节课:")
                        print(f"课程名称: {schedule['courseName']}")
                        week_binary = bin(int(schedule["courseWeek"]))[2:][::-1]
                        time_binary = bin(int(schedule["courseTime"]))[2:]
                        time_day = int(time_binary[:-12], 2)
                        time_section_binary = time_binary[-12:][::-1]
                        time_section_list = []
                        week_list = []
                        for idx, bit in enumerate(time_section_binary):
                            if bit == '1':
                                time_section_list.append(idx + 1)
                        for idx, bit in enumerate(week_binary):
                            if bit == '1':
                                week_list.append(idx + 1)

                        number2day = {1: "星期一", 2: "星期二", 3: "星期三", 4: "星期四", 5: "星期五", 6: "星期六", 7: "星期日"}
                        print(number2day[time_day])
                        print("上课时间段: ", time_section_list)
                        print("上课周数: ", week_list)
                        print("上课地点: ", schedule["coursePlace"])

                        section_to_time = self.section_time_table

                        for week in week_list:
                            event_list.append({
                                "event_name": schedule["courseName"],
                                "event_location": schedule["coursePlace"],
                                "event_week": week,
                                "event_day": time_day,
                                "event_time": (
                                    section_to_time[time_section_list[0]][0],
                                    section_to_time[time_section_list[-1]][1]
                                )
                            })

                    print("-------")

            else:
                self.session.close()
                raise Exception(f'redirect error code: {response.status_code}, {response.text}')
        else:
            self.session.close()
            raise Exception(f'get redirect url error code: {response.status_code}, {response.text}')

        # Read ics content from "course_schedule_tmp.ics"
        ics_content = ""
        with open("course_schedule_tmp.ics", "r") as f:
            ics_content = f.read()
        
        for event in event_list:
            ics_content += self.convert_event_to_ics_format(
                event["event_name"],
                event["event_location"],
                event["event_week"],
                event["event_day"],
                event["event_time"]
            )
        
        ics_content += "END:VCALENDAR\n"

        # save ics file
        with open("course_schedule.ics", "w", encoding='utf-8') as f:
            f.write(ics_content)

        print(f"\n本科课程总数: {course_count} 节")

    
    @detailException
    def convert_event_to_ics_format(self, event_name, event_location, event_week, event_day, event_time):
        # Format: event_name, event_location: string
        # event_week: int, 1 represents the week whose Monday lies on  2024-8-26
        # event_day: int, 1 represents Monday
        # event_time: tuple<string, string>, (start_time, end_time), e.g. ("08:00", "09:50")

        # Calculate the date of the event
        date = self.semester_start_date 
        date = date.split("-")
        date = list(map(int, date))
        date = datetime.date(date[0], date[1], date[2])
        
        
        date += datetime.timedelta(weeks=event_week - 1)
        date += datetime.timedelta(days=event_day - 1)
        date = date.strftime("%Y%m%d")

        # Calculate the start time and end time of the event
        start_time = event_time[0].split(":")
        start_time = list(map(int, start_time))
        start_time = datetime.time(start_time[0], start_time[1])
        end_time = event_time[1].split(":")
        end_time = list(map(int, end_time))
        end_time = datetime.time(end_time[0], end_time[1])

        # Format the event
        event = f"BEGIN:VEVENT\n"
        event += f"SUMMARY:{event_name}\n"
        event += f"LOCATION:{event_location}\n"
        event += f"DTSTART;TZID=Asia/Shanghai:{date}T{start_time.strftime('%H%M%S')}\n"
        event += f"DTEND;TZID=Asia/Shanghai:{date}T{end_time.strftime('%H%M%S')}\n"
        event += f"END:VEVENT\n"

        return event

    # @detailException
    # def convert_event_to_ics_format_master(self, event_name, event_location, event_week, event_day, event_time, reminder_minutes):
    #     # Format: event_name, event_location: string
    #     # event_week: int, 1 represents the week whose Monday lies on 2024-8-26
    #     # event_day: int, 1 represents Monday
    #     # event_time: tuple<string, string>, (start_time, end_time), e.g. ("08:00", "09:50")
    #     # reminder_minutes: int, the number of minutes before the event to trigger the reminder
        
    #     # Calculate the date of the event
    #     date = "2024-08-26"
    #     date = date.split("-")
    #     date = list(map(int, date))
    #     date = datetime.date(date[0], date[1], date[2])
    #     date += datetime.timedelta(weeks=event_week - 1)
    #     date += datetime.timedelta(days=event_day - 1)
    #     date = date.strftime("%Y%m%d")

    #     # Calculate the start time and end time of the event
    #     start_time = event_time[0].split(":")
    #     start_time = list(map(int, start_time))
    #     start_time = datetime.time(start_time[0], start_time[1])
    #     end_time = event_time[1].split(":")
    #     end_time = list(map(int, end_time))
    #     end_time = datetime.time(end_time[0], end_time[1])

    #     # Format the event with reminder
    #     event = f"BEGIN:VEVENT\n"
    #     event += f"SUMMARY:{event_name}\n"
    #     event += f"LOCATION:{event_location}\n"
    #     event += f"DTSTART;TZID=Asia/Shanghai:{date}T{start_time.strftime('%H%M%S')}\n"
    #     event += f"DTEND;TZID=Asia/Shanghai:{date}T{end_time.strftime('%H%M%S')}\n"
        
    #     # Add dynamic reminder based on input reminder_minutes
    #     event += f"""
    # BEGIN:VALARM
    # ACTION:DISPLAY
    # DESCRIPTION:Reminder
    # TRIGGER:-PT{reminder_minutes}M
    # END:VALARM
    # """

    #     event += f"END:VEVENT\n"

    #     return event

    @detailException
    def launch(self):
        logger.log('---------------')
        logger.log('start')
        try:
            self.__login()
            self.__get_course_data()
        except Exception as e:
            logger.log(f'error: {e}')

        logger.log('finish')
        logger.log('---------------')

    @detailException
    def login_and_get_data(self):
        logger.log('---------------')
        logger.log('start')
        try:
            self.__login()
            course_name_and_link = self.get_master_course_name_and_time_link()
            logger.log(course_name_and_link)
        except Exception as e:
            logger.log(f'error: {e}')

        logger.log('finish')
        logger.log('---------------')

    def print_schedule_table(self, course_list):
        # 创建一个 7x12 的二维数组来存储课程信息
        schedule = [[[] for _ in range(12)] for _ in range(7)]
        
        # 将课程信息按课程名称分组
        course_groups = {}
        for course in course_list:
            key = (course["event_name"], course["event_day"], course["event_location"])
            if key not in course_groups:
                course_groups[key] = {
                    "name": course["event_name"],
                    "day": course["event_day"] - 1,
                    "location": course["event_location"],
                    "sections": []
                }
            
            # 找到对应的节数
            time_start = course["event_time"][0]
            time_end = course["event_time"][1]
            
            for section, times in self.master_section_time_table.items():
                if times[0] == time_start:
                    start_section = int(section) - 1
                    break
            for section, times in self.master_section_time_table.items():
                if times[1] == time_end:
                    end_section = int(section) - 1
                    break
                
            course_groups[key]["sections"].extend(range(start_section, end_section + 1))
        
        # 将分组后的课程信息填入课表
        for course_info in course_groups.values():
            day = course_info["day"]
            sections = sorted(set(course_info["sections"]))  # 去重并排序
            if sections:
                start_section = sections[0]
                end_section = sections[-1]
                # 简化课程信息显示
                course_text = f"{course_info['name']}\n{course_info['location']}"
                schedule[day][start_section].append(course_text)
        
        # 修改表格样式设置
        table = PrettyTable()
        table.field_names = ["节次", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        
        # 添加每节课的信息
        for section in range(12):
            row = [f"第{section+1}节"]  # 更改节次显示格式
            for day in range(7):
                if schedule[day][section]:
                    # 优化课程信息显示格式
                    course_info = schedule[day][section][0].split('\n')
                    cell = f"{course_info[0]}\n@{course_info[1]}"  # 添加@符号来分隔课程名和地点
                else:
                    cell = ""
                row.append(cell)
            table.add_row(row)
        
        # 优化表格样式
        table.align = "c"  # 居中对齐
        table.max_width = 15  # 增加单元格宽度
        table.padding_width = 1  # 设置内边距
        table.hrules = 1  # 添加水平分隔线
        
        print("\n=== 课程表 ===")
        print(table)

