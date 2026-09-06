#!/usr/bin/env python3    # -*- coding: utf-8 -*

"""
main.py 南科大TIS喵课助手

@CreateDate 2021-1-9
@UpdateDate 2024-9-9
"""

import _thread
import time
import os
from getpass import getpass
from json import loads, dumps
from re import findall

import requests
from colorama import init

import sys
import warnings
from urllib3.exceptions import InsecureRequestWarning


def warn(message, category, filename, lineno, _file=None, line=None):
    if category is not InsecureRequestWarning:
        sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))

CLASS_CACHE_PATH = "class.txt"
COURSE_INFO_PATH = "course.txt"
USER_INFO_PATH = "user.txt"
warnings.showwarning = warn
SUCCESS = "[\x1b[0;32m+\x1b[0m] "
STAR = "[\x1b[0;32m*\x1b[0m] "
ERROR = "[\x1b[0;31mx\x1b[0m] "
INFO = "[\x1b[0;36m!\x1b[0m] "
FAIL = "[\x1b[0;33m-\x1b[0m] "
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
head = {
    "user-agent": UA,
    "x-requested-with": "XMLHttpRequest"
}

COURSE_TYPE = {'bxxk': "通识必修选课", 'xxxk': "通识选修选课", "kzyxk": '培养方案内课程',
               "zynknjxk": '非培养方案内课程', "cxxk": '重修选课', "jhnxk": '计划内选课新生'}

TIMEOUT = 1.2 # 线程喵课间隔

course_list = []  # 需要喵的课程队列
# 由于Tis的新限制，逻辑改为同时只选一门课

def load_course():
    """ 用于加载本地要喵的课程
    如果存在文件就读文件里的，不存在就手动录入
    有些(我忘了是哪些了)情况会在文件头会有几个不可见字符，但是会被python读进来，所以第一行建议忽略留空"""
    courses = []
    if os.path.exists(CLASS_CACHE_PATH) and os.path.isfile(CLASS_CACHE_PATH):
        print(INFO + "读取规划课表...")
        with open(CLASS_CACHE_PATH, "r", encoding="utf8") as f:
            courses = f.readlines()
        print(SUCCESS + "规划课表读取完毕")
    else:
        print(FAIL + "没有找到规划课表，请手动输入课程信息，输入-1结束录入")
        s = "===本文件是待喵课程的列表，一行输入一个课程名字==请勿删除本行==="
        while s != "-1":
            courses.append(s)
            s = input()
        s = input(INFO + "是否保存录入的信息（y/N）？")
        if s in "yY":
            with open(CLASS_CACHE_PATH, "w", encoding="utf8") as f:
                f.writelines('\n'.join(courses))
    return courses


def cas_login(sid, pwd):
    """ 用于和南科大CAS认证交互，拿到tis的有效cookie
    输入用于CAS登录的用户名密码，输出tis需要的SESSION cookie内容
    2026-09起TIS不再下发route/JSESSIONID，改为Spring Session的SESSION cookie """
    print(INFO + "测试CAS链接...")
    try:  # Login 服务的CAS链接有时候会变
        login_url = "https://cas.sustech.edu.cn/cas/login?service=https%3A%2F%2Ftis.sustech.edu.cn%2Fcas"
        req = requests.get(login_url, headers=head, verify=False)
        assert (req.status_code == 200)
        print(SUCCESS + "成功连接到CAS...")
    except Exception as ex:
        print(ERROR + f"不能访问CAS, 请检查您的网络连接状态 ({ex})")
        return ""
    print(INFO + "登录中...")
    data = {  # execution大概是CAS中前端session id之类的东西
        'username': sid,
        'password': pwd,
        'execution': str(req.text).split('''name="execution" value="''')[1].split('"')[0],
        '_eventId': 'submit',
        'geolocation': ''  # 新字段
    }
    while True:
        req = requests.post(login_url, data=data, allow_redirects=False, headers=head, verify=False)
        if req.status_code == 500:
            print(ERROR + "CAS服务出错，重试中")
        break
    if "Location" in req.headers.keys():
        print(SUCCESS + "登录成功")
    else:
        print(ERROR + "用户名或密码错误，请检查")
        return ""
    # 2026-09 TIS升级为Spring Session，CAS回跳后下发的是SESSION cookie(不再是route/JSESSIONID)
    # 用Session跟随全部重定向以累积每一跳的Set-Cookie，再从cookie池提取SESSION
    sess = requests.Session()
    res = sess.get(req.headers["Location"], headers=head, verify=False)
    _session = sess.cookies.get("SESSION")
    if not _session or "cas.sustech.edu.cn" in res.url:
        print(ERROR + "TIS未返回有效会话(SESSION)，接口可能又有变动")
        return ""
    return _session


def getinfo(semester_data):
    """ 用于向tis请求当前学期的课程ID，得到的ID将用于选课的请求
    输入当前学期的日期信息，返回的json包括了课程名和内部的ID """
    if os.path.exists(COURSE_INFO_PATH) and os.path.isfile(COURSE_INFO_PATH):
        print(INFO + f"读取本地缓存的课程信息，如果需要更新请删除{COURSE_INFO_PATH}文件")
        with open(COURSE_INFO_PATH, "r", encoding="utf8") as f:
            cached_course_list = f.readlines()
        try:
            cached_time = cached_course_list[0].strip()
            if cached_time == semester_data['p_xnxq']:
                _course_info = loads(cached_course_list[1])
                print(SUCCESS + f"课程信息读取完毕，共读取{str(len(_course_info))}门课程信息\n")
                return _course_info
            else:
                print(INFO + "缓存文件已过期，重新获取课程信息")
        except Exception as ex:
            print(ERROR + f"缓存文件损坏，重新获取课程信息，{ex}")
    print(INFO + "从服务器下载课程信息，请稍等...")
    _course_info = {}
    for c_type in COURSE_TYPE.keys():
        data = {
            "p_xn": semester_data['p_xn'],  # 当前学年
            "p_xq": semester_data['p_xq'],  # 当前学期
            "p_xnxq": semester_data['p_xnxq'],  # 当前学年学期
            "p_pylx": 1,
            "mxpylx": 1,
            "p_xkfsdm": c_type,
            "pageNum": 1,
            "pageSize": 1000  # 每学期总共开课在1000左右，所以单分类可以包括学期的全部课程
        }
        print("[\x1b[0;36m*\x1b[0m] " + f"获取 {COURSE_TYPE[c_type]} 列表...")
        # 2026-09 TIS启用了用户级查询限流(返回"查询请求频率过高")，需要等待退避重试
        attempt, _kxrw = 0, None
        while True:
            req = requests.post('https://tis.sustech.edu.cn/Xsxk/queryKxrw', data=data, headers=head, verify=False)
            raw_class_data = loads(req.text)
            if raw_class_data.get('jg') == '-1' or '频率过高' in str(raw_class_data.get('message', '')):
                attempt += 1
                if attempt > 15:
                    print(FAIL + f"  {c_type} 多次重试仍被限流，暂时跳过(删除{COURSE_INFO_PATH}后重跑可补齐)")
                    break
                _wait = min(3 + attempt * 2, 15)
                print(f"    [!] {c_type} 被限流，{_wait}秒后重试(第{attempt}次)...")
                time.sleep(_wait)
                continue
            _kxrw = raw_class_data.get('kxrwList')
            break
        if _kxrw and _kxrw.get('list') is not None:
            print(f"    [+] {c_type}: 返回{len(_kxrw['list'])}门 (total={_kxrw.get('total')})")
            for i in _kxrw['list']:
                _course_info[i['rwmc']] = (i['id'], c_type)
        else:
            print(f"    [debug] {c_type}: 未返回课程列表")
        time.sleep(2)  # 类别之间稍作间隔，降低触发限流的概率
    print(SUCCESS + f"课程信息读取完毕，共读取{str(len(_course_info))}门课程信息")
    s = input(INFO + "是否保存读取的课程信息（y/N）？")
    if s in "yY":
        with open(COURSE_INFO_PATH, "w", encoding="utf8") as f:
            f.write(str(semester_data['p_xnxq']) + "\n")
            f.write(dumps(_course_info, ensure_ascii=False))
    return _course_info


def submit(semester_data, loop=3):
    """ 用于向tis发送喵课的请求
    这里假设主要耗时在网络IO上，本地处理时间几乎可以忽略
    （什么，购物车是怎么回事？那首先排除教务系统是个魔改的电商项目）"""
    for _ in range(loop):
        if not course_list:
            print(SUCCESS + "⌯'ㅅ'⌯所有课程已喵完，再见😾")
            exec("os._exit(0)")  # lint hack
        c_id, c_type, c_name = course_list[0]
        data = {
            "p_pylx": 1,
            "p_xktjz": "rwtjzyx",  # 提交至，可选任务，rwtjzgwc提交至购物车，rwtjzyx提交至已选 gwctjzyx购物车提交至已选
            "p_xn": semester_data['p_xn'],
            "p_xq": semester_data['p_xq'],
            "p_xnxq": semester_data['p_xnxq'],
            "p_xkfsdm": c_type,  # 选课方式
            "p_id": c_id,  # 课程id
            "p_sfxsgwckb": 1,  # 固定
        }
        req = requests.post('https://tis.sustech.edu.cn/Xsxk/addGouwuche', data=data, headers=head, verify=False)
        res = loads(req.text)['message']
        if "成功" in req.text:
            print("[\x1b[0;34m{}\x1b[0m]".format("=" * 50), flush=True)
            print("[\x1b[0;34m█\x1b[0m]\t\t\t" + res, flush=True)
            print("[\x1b[0;34m{}\x1b[0m]".format("=" * 50), flush=True)
            course_list.pop(0)
        else:
            print("[\x1b[0;30m-\x1b[0m]\t\t\t" + res, flush=True)
        # 注意:此处不能包含"已选"——叠加抢同一门课时,后到的重复请求会返回"已选"并误删队列中的下一门课
        if any(map(lambda x: x in req.text, ["冲突", "已满"])):
            print(f"[\x1b[0;31m!\x1b[0m] ({c_name})因为({res})跳过", flush=True)
            course_list.pop(0)
        if "频率过高" in req.text:
            time.sleep(3)  # 被限流时额外等待，避免连续请求持续触发限制
        time.sleep(TIMEOUT)
        
        
def submit_sequential(semester_data):
    """ 按照输入课程顺序向tis发送喵课请求 """
    if not course_list:
        print(SUCCESS + "⌯'ㅅ'⌯所有课程已喵完，再见😾")
        exec("os._exit(0)")  # lint hack
    course_list_copy = course_list.copy()
    for course in course_list_copy:
        c_id, c_type, c_name = course
        if course in course_list:
            data = {
                "p_pylx": 1,
                "p_xktjz": "rwtjzyx",  # 提交至，可选任务，rwtjzgwc提交至购物车，rwtjzyx提交至已选 gwctjzyx购物车提交至已选
                "p_xn": semester_data['p_xn'],
                "p_xq": semester_data['p_xq'],
                "p_xnxq": semester_data['p_xnxq'],
                "p_xkfsdm": c_type,  # 选课方式
                "p_id": c_id,  # 课程id
                "p_sfxsgwckb": 1,  # 固定
            }
            req = requests.post('https://tis.sustech.edu.cn/Xsxk/addGouwuche', data=data, headers=head, verify=False)
            res = loads(req.text)['message']
            if "成功" in req.text:
                print("[\x1b[0;34m{}\x1b[0m]".format("=" * 50), flush=True)
                print("[\x1b[0;34m█\x1b[0m]\t\t\t" + res, flush=True)
                print("[\x1b[0;34m{}\x1b[0m]".format("=" * 50), flush=True)
                course_list.remove(course)
            else:
                print("[\x1b[0;30m-\x1b[0m]\t\t\t" + res, flush=True)
            # 同上,不含"已选",避免并发重复请求误删队列中的后续课程
            if any(map(lambda x: x in req.text, ["冲突", "已满"])):
                print(f"[\x1b[0;31m!\x1b[0m] ({c_name})因为({res})跳过", flush=True)
                course_list.remove(course)
            if "频率过高" in req.text:
                time.sleep(3)  # 被限流时额外等待，避免连续请求持续触发限制
            time.sleep(TIMEOUT)


def exit():
    """ 退出函数 """
    print(INFO + "退出喵课助手，再见😾")
    exec("os._exit(0)")  # lint hack


if __name__ == '__main__':
    init(autoreset=True)  # 某窗口系统的优质终端并不直接支持如下转义彩色字符，所以需要一些库来帮忙
    course_name_list = load_course()  # 读取本地待喵的课程
    # 下面是CAS登录
    session_id = ""
    if os.path.exists(USER_INFO_PATH): # 如果有保存的用户信息，尝试从文件自动登录
        try:
            with open(USER_INFO_PATH, "r", encoding="utf8") as f:
                lines = f.read().splitlines()
                if len(lines) >= 2:
                    user_name, pass_word = lines[0], lines[1]
                    session_id = cas_login(user_name, pass_word)
        except Exception as e:
            print(FAIL + f"自动登录出现异常: {e}")
        if session_id == "":
            print(FAIL + "自动登录失败，需要手动登录")

    while session_id == "":
        user_name = input("请输入您的学号：")  # getpass在PyCharm里不能正常工作，请改为input或写死
        pass_word = getpass("请输入CAS密码（密码不显示，输入完按回车即可）：")
        session_id = cas_login(user_name, pass_word)
        if session_id == "":
            print(FAIL + "请重试...")
        else: # 登录成功后询问保存
            s = input(INFO + "是否保存用户信息（y/N）？")
            if s.lower() in {"y", "yes"}:
                with open(USER_INFO_PATH, "w", encoding="utf8") as f:
                    f.write(f"{user_name}\n{pass_word}")
    head['cookie'] = f'SESSION={session_id};'
    # 下面先获取当前的学期
    print(INFO + "从服务器获取当前喵课时间...")
    semester_info = None
    for _try in range(10):  # 这里要加mxpylx才能获取到选课所在最新学期；限流时退避重试
        _raw = loads(requests.post('https://tis.sustech.edu.cn/Xsxk/queryXkdqXnxq',
                                   data={"mxpylx": 1}, headers=head, verify=False).text)
        if _raw.get('jg') == '-1' or '频率过高' in str(_raw.get('message', '')):
            print(FAIL + " 学期查询被限流，5秒后重试...")
            time.sleep(5)
            continue
        semester_info = _raw
        break
    if semester_info is None:
        print(ERROR + "无法获取学期信息，请稍后重跑")
        exit()
    print(SUCCESS + f"当前学期是{semester_info['p_xn']}学年第{semester_info['p_xq']}学期，为"
                    f"{['', '秋季', '春季', '小'][int(semester_info['p_xq'])]}学期")
    # 然后获取本学期全部课程信息
    print(INFO + "读取课程信息...")
    course_info = getinfo(semester_info)
    # 分析要喵课程的ID
    for name in course_name_list:
        name = name.strip()
        if name in course_info.keys():
            course_id, course_type = course_info[name]
            course_list.append([course_id, course_type, name])
    print("[\x1b[0;34m{}\x1b[0m]".format("=" * 25))
    for course in course_list:
        print(f"{COURSE_TYPE[course[1]]} : {course[2]}\t\tID为: {course[0]}")
    print("[\x1b[0;34m{}\x1b[0m]".format("=" * 25))
    print(SUCCESS + "成功读入以上信息\n")
    # 喵课主逻辑

    if not course_list:
        print("没有读取到要喵的课程，请检查课程名称是否正确")
        exit()
    
    mode = input("请输入喵课模式：[1] -- 优先按照输入课程顺序喵课，2 -- 所有课程循环喵课，0 -- 退出\n") or "1"
    
    while True:

        if mode == "1":
            print(INFO + "当前模式: 优先按照输入课程顺序喵课")
            while course_list:
                if input(STAR + "按一下回车喵三次，多按同时喵多次，任意字符跳过当前课程\n"):
                    course_list.pop(0)
                    if not course_list:
                        print(SUCCESS + "⌯'ㅅ'⌯所有课程已喵完，再见😾")
                        exec("os._exit(0)")
                try:
                    _thread.start_new_thread(submit, (semester_info, 3))
                except Exception as e:
                    print(f"[{e}] 线程异常")
        
        if mode == "2":
            print(INFO + "当前模式: 所有课程循环喵课")
            while course_list:
                if input(STAR + "按一下回车对所有课程喵一次，多按同时喵多次，任意字符退出\n"):
                    exit()
                try:
                    _thread.start_new_thread(submit_sequential, (semester_info,))
                except Exception as e:
                    print(f"[{e}] 线程异常")
        
        if mode == "0":
            exit()
