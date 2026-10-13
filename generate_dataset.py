#!/usr/bin/env python3
"""Generate 1,300 original, synthetic, deterministic System One benchmark cases.
No copyrighted third-party dataset text is included. These are synthetic labels,
not human verified; for publishable quality comparison combine with public datasets.
"""
import csv, json, random, hashlib
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data'
OUT.mkdir(exist_ok=True)
R=random.Random(20261013)
cases=[]


def add(category, language, state, kind, instructions, criteria, gold, options=None, meta=None):
    cid=f'S1-{len(cases)+1:05d}'
    q={'type':kind,'instructions':instructions}
    if kind!='noul': q['criteria']=criteria
    record={'id':cid,'category':category,'language':language,'state':state,
            'question':q,'gold':gold,'gold_source':'synthetic_rule',
            'metadata':meta or {}}
    if kind=='choice' and gold not in criteria:
        raise ValueError((cid,'bad choice gold',gold))
    if kind=='score' and (not isinstance(gold,int) or not 0<=gold<len(criteria)):
        raise ValueError((cid,'bad score gold',gold))
    if kind=='noul' and gold not in (0,1):
        raise ValueError((cid,'bad noul gold',gold))
    cases.append(record)
    return record

common_noise_en=[
    'Reference {ref}; this request was received via the web form.',
    'Case {ref}; the user previously contacted another department about an unrelated issue.',
    'Ticket {ref}; do not infer the intent from the ticket number.',
    'Request {ref}; the notes contain no additional instructions.',
]
common_noise_zh=[
    '工单编号{ref}，由移动端提交。',
    '请求编号{ref}，之前咨询的其他问题与本次无关。',
    '编号{ref}，请依据实际诉求判断。',
    '工单{ref}，其余备注不影响核心事项。',
]
def vary(base, idx, language):
    x=(common_noise_zh if language=='zh' else common_noise_en)[idx%4]
    ref=f'{(idx*7919+4391)%90000+10000}'
    return f'{base} {x.format(ref=ref)}'

support={
'billing':('Charges, payment discrepancies, refunds / 扣费、账单、退款',
 ['I was charged twice for one renewal.','My invoice total is larger than the confirmed price.','Please refund the extra card transaction.','My payment receipt lists an unknown fee.','Can you explain this unexpected charge?',
 '订阅续费被重复扣款了。','发票总额和确认的价格不同。','请退回多扣的银行卡交易金额。','收据里出现一笔不认识的费用。','为什么这期账单多了一项收费？']),
'account_access':('Login, password, MFA and authentication / 登录、密码、身份验证',
 ['My account login keeps rejecting the correct password.','I lost access to my MFA device.','The password reset link has expired.','The sign-in page says my account is locked.','I cannot receive the verification code.',
 '账号使用正确密码仍无法登录。','双因素验证设备遗失了。','重置密码的链接已失效。','登录页面提示账户已锁定。','一直收不到登录验证码。']),
'product_bug':('Software defects, crashes and API errors / 产品故障、接口异常',
 ['The dashboard crashes after I click Export.','Your API returns a 500 on valid requests.','The search results are blank despite matching records.','The app freezes on the second upload.','The latest release broke the reporting page.',
 '仪表盘点击导出就闪退。','合法的API请求持续返回500。','明明有匹配记录却搜索不到结果。','应用第二次上传时卡死。','升级后报表页面打不开。']),
'feature_request':('Requests for product enhancements / 新功能建议',
 ['Please add a dark mode to the dashboard.','Could the app support scheduled exports?','We would like a batch delete option.','Please implement a keyboard shortcut for search.','It would help to export to Markdown.',
 '希望仪表盘增加深色模式。','能否支持定时导出报表？','希望增加批量删除功能。','请增加搜索快捷键。','建议支持Markdown格式导出。']),
'sales':('Pricing, quotes, plans and purchasing / 报价、购买、方案',
 ['Please send a quote for 200 seats.','What discount applies to an annual enterprise plan?','We need to compare available subscription tiers.','Can I arrange a sales demo before purchase?','What is the price for a volume contract?',
 '请提供200个账号的采购报价。','企业年度套餐有什么折扣？','想对比你们的订阅档位。','购买前能预约产品演示吗？','批量采购合同是什么价格？']),
'data_privacy':('Personal data access, deletion, export / 个人信息、隐私权利',
 ['Please erase the personal data associated with my profile.','I need an export of the personal information stored about me.','How can I revoke my data-processing consent?','Requesting deletion of my customer record.','I want to know what personal data your company retains.',
 '请删除我账号关联的个人数据。','我要导出平台保存的个人信息。','如何撤回个人数据处理授权？','申请清除我的客户档案。','请列出平台目前保存了哪些个人信息。']),
'order_shipping':('Delivery tracking, shipping, address changes / 物流配送',
 ['The parcel tracking number does not update.','Please change the shipping address for my order.','When will the courier deliver my package?','The package arrived at the wrong location.','Where is my order in transit?',
 '包裹物流单号一直没有更新。','请修改订单收货地址。','快递什么时候派送？','包裹被送到了错误地点。','订单在途物流状态在哪里查看？']),
'cancel_subscription':('Terminate recurring subscriptions and auto renewal / 取消订阅、自动续费',
 ['Please cancel next month’s auto-renewal.','I want to stop my recurring subscription.','Turn off automatic renewal for this plan.','Do not extend my membership after this period.','Please terminate the subscription at its expiry.',
 '请取消下个月的自动续订。','想停止我的周期性会员订阅。','请关闭当前套餐的自动续费。','会员本期结束后不要延长。','请在到期时终止订阅服务。'])
}
support_options={k:v[0] for k,v in support.items()}
for label,(desc, examples) in support.items():
    for i in range(20):
        language='en' if i%10<5 else 'zh'
        state=vary(examples[i%10],i,language)
        add('customer_support',language,state,'choice',
            'Which team should own this customer request?' if language=='en' else '这条客户诉求应交给哪个团队处理？',
            support_options,label,meta={'intended_domain':'customer_service'})

tools={
'web_search':('Search public internet sources and news / 搜索公开网页',
 ['Find the latest public release notes of the database.','Search for official documentation of a new API.','Look up recent security advisories on the web.','Find a publicly available research paper.','Research which vendors offer this public product.',
 '搜索数据库最新的公开发行说明。','查找新接口的官方在线文档。','检索近期公开安全公告。','找到某篇公开发表的研究论文。','搜索哪些厂商提供这种产品。']),
'calculator':('Compute exact arithmetic expressions / 精确算术计算',
 ['Calculate 341 multiplied by 27.','Compute a 12 percent tax on 830 units.','Find the median of 7, 13, 16 and 40.','Calculate the monthly total given six equal payments.','Compute the ratio of 77 to 220.',
 '计算341乘以27。','求830元的12%税额。','计算7、13、16、40的中位数。','六笔相等付款求月合计。','计算77与220的比值。']),
'calendar':('Create, move or inspect calendar events / 日历事件',
 ['Move my project check-in to next Thursday.','Check whether my calendar is free tomorrow at 3 PM.','Schedule a design review for the team.','Cancel the event called weekly sync.','Add a reminder event to my work calendar.',
 '把项目例会移到下周四。','查看明天下午三点是否有空档。','安排团队设计评审日程。','取消名为每周同步的日历事件。','在工作日历中新增一个提醒事项。']),
'email':('Read, draft, send and search email / 邮件收发及检索',
 ['Draft a reply to the last vendor email.','Search my inbox for a purchase receipt.','Forward the contract email to my manager.','Find unread messages from accounting.','Send a reminder email to the partner.',
 '回复厂商上一封邮件草稿。','在收件箱查找采购收据邮件。','把合同邮件转发给主管。','搜索财务发来的未读邮件。','给合作方发送提醒邮件。']),
'database_query':('Query internal tabular SQL records / 内部数据库查询',
 ['Query the orders table for pending payments.','Count rows in the internal event log.','Select recent customer transactions in SQL.','Run an aggregation on our private inventory database.','Fetch the records for a known internal order ID.',
 '查询数据库订单表中的待付款记录。','统计内部事件日志表行数。','通过SQL选择最近的客户交易记录。','对内部库存数据库进行聚合。','根据内部订单号查询对应记录。']),
'code_executor':('Execute code for programming or data processing / 执行程序代码',
 ['Run Python to parse this JSON collection.','Execute the unit tests in the repository.','Use code to deduplicate these CSV rows.','Run a shell script to validate file checksums.','Execute a script that transforms the data structure.',
 '运行Python解析这一批JSON数据。','执行代码仓库的单元测试。','写代码去重CSV中的重复行。','运行脚本验证文件校验和。','执行程序转换现有数据结构。']),
'file_reader':('Inspect attached local files / 读取附件或本地文件',
 ['Open the uploaded PDF and locate section four.','Extract the contents of this attached spreadsheet.','Read the local markdown specification.','Summarize the provided Word file.','Inspect the JSON file attached to this request.',
 '打开上传的PDF并找到第四节。','提取当前附件电子表格的数据。','读取本地Markdown需求文档。','总结上传的Word文件。','检查这次请求附带的JSON文件。']),
'image_analyzer':('Inspect or understand existing images / 图像理解',
 ['Identify the objects in this attached photo.','Read the chart from the uploaded screenshot.','Determine whether a traffic sign appears in the photo.','Explain the diagram in this picture.','Analyze the visual layout of the attached image.',
 '识别上传照片中的物体。','查看截图里的统计图信息。','判断照片中有没有交通标志。','解释这张图片中的流程图。','分析附件图片的页面布局。'])
}
tool_opts={k:v[0] for k,v in tools.items()}
for label,(desc, examples) in tools.items():
    for i in range(20):
        lang='en' if i%10<5 else 'zh'
        add('tool_routing',lang,vary(examples[i%10],i,lang),'choice',
            'Which single tool is most appropriate for the task?' if lang=='en' else '这项任务最适合调用哪个工具？',
            tool_opts,label)

incident={
'restart_service':['The API process is crashed, deployment artifacts are intact, and restarting the process is approved.','进程已退出，部署包完整，并且授权执行服务重启。'],
'rollback_release':['Errors began immediately after a deployment; the previous stable build is available and rollback is approved.','发布后立即出现持续错误，稳定旧版本可用并已批准回滚。'],
'scale_workers':['The queue is growing, all workers are healthy but saturated, and additional capacity is available.','队列持续积压，工作进程均正常但负载饱和，现有资源可横向扩容。'],
'rotate_credentials':['Audit logs confirm a production service credential was exposed; follow the approved rotation procedure.','审计日志确认生产密钥发生泄露，需要执行已批准的轮换流程。'],
'collect_logs':['Failure symptoms are unclear and there is not yet enough evidence to change the service.','故障现象尚不明确，当前证据不足以调整服务，应先收集日志。'],
'escalate_human':['An outage involves payment reconciliation; changing configuration requires explicit approval that is absent.','支付对账故障涉及高风险配置变更，尚未获得变更授权，需人工升级处理。']
}
incident_opts={
'restart_service':'Restart approved failed service / 重启已获授权的故障服务',
'rollback_release':'Rollback to last stable release / 回滚稳定版本',
'scale_workers':'Add compute workers for healthy saturated queue / 扩容工作进程',
'rotate_credentials':'Rotate confirmed compromised credential / 轮换泄露密钥',
'collect_logs':'Collect diagnostics before taking action / 收集诊断信息',
'escalate_human':'Escalate high-risk case for approval / 高风险人工升级'
}
for label, examples in incident.items():
    for i in range(20):
        lang='en' if i%2==0 else 'zh'
        extra=(f' Service pool={i%5+1}; affected region=zone-{i%3+1}.' if lang=='en' else f' 服务池编号={i%5+1}，区域=zone-{i%3+1}。')
        add('incident_response',lang,examples[i%2]+extra,'choice',
            'Choose the next operational action under the stated approvals.' if lang=='en' else '依据已有授权选择下一步运维动作。',incident_opts,label)

security={
'allow':['User asks for the public documentation link and no credentials or personal records are present.','请求仅涉及公开帮助文档，不包含任何凭据或个人记录。'],
'review':['A request asks for access to an internal report but the identity proof is incomplete; no data has been shared.','有人索取内部报告，但身份验证材料尚不完整，尚未发生数据披露。'],
'block':['Message explicitly instructs the agent to reveal a production API secret to an external mailbox.','消息明确要求把生产API密钥发送给外部邮箱。'],
'escalate':['Security telemetry indicates an administrator account was compromised and data may have been exported.','安全监控发现管理员账号疑似已被攻破，且可能发生数据外传。']
}
security_opts={
'allow':'Safe request: proceed / 正常放行',
'review':'Ambiguous authorization: manual review / 授权不明确，待人工复核',
'block':'Disallowed disclosure or instruction: block / 拒绝违规操作',
'escalate':'Active suspected compromise: incident escalation / 安全事件升级'
}
for label,examples in security.items():
    for i in range(30):
        lang='en' if i%2==0 else 'zh'
        context=f' Audit id SEC-{74000+i*19}; no extra evidence.' if lang=='en' else f' 审计编号SEC-{74000+i*19}，暂无其他补充材料。'
        add('security_triage',lang,examples[i%2]+context,'choice',
            'Classify the correct security workflow disposition.' if lang=='en' else '判定正确的安全处理流程。',security_opts,label)

commerce={
'create_order':['Customer confirmed items and address, payment is not yet requested; create the purchase order.','客户已确认商品和地址，尚未发起付款，需要创建采购订单。'],
'request_payment':['The order exists but is unpaid; the customer asks for a secure payment link.','订单已创建但未付款，客户请求提供合规付款链接。'],
'issue_refund':['Payment settled, return inspected and accepted; a refund is authorized.','交易已完成，退货验收通过，财务已批准退款。'],
'update_address':['The package is not dispatched, customer verified their identity and needs delivery address correction.','包裹尚未发货，客户身份已验证并请求修改配送地址。'],
'track_delivery':['Order shipped; customer only wants latest courier tracking status.','订单已经发出，客户仅需要查看快递物流状态。'],
'cancel_order':['Unpaid order has not shipped; customer explicitly requests cancellation.','未付款订单尚未发货，客户明确要求取消订单。']
}
commerce_opts={
'create_order':'Create order after confirmation / 创建订单',
'request_payment':'Provide secure payment process / 引导付款',
'issue_refund':'Issue approved refund / 退款',
'update_address':'Correct undelivered order address / 更改地址',
'track_delivery':'Look up tracking status / 查询物流',
'cancel_order':'Cancel unfulfilled order / 取消订单'
}
for label, examples in commerce.items():
    for i in range(20):
        lang='en' if i%2==0 else 'zh'
        suffix=f' Order reference O-{29000+i*11}.' if lang=='en' else f' 订单参考号O-{29000+i*11}。'
        add('commerce_workflow',lang,examples[i%2]+suffix,'choice',
            'Which order-processing action is appropriate now?' if lang=='en' else '现在应执行哪个订单处理动作？',commerce_opts,label)

# Fact-based binary evidence: answer is a deterministic comparison with text facts.
items_en=['Alpha','Beta','Gamma','Delta','Epsilon','Zeta']
items_zh=['甲','乙','丙','丁','戊','己']
for i in range(120):
    lang='en' if i%2==0 else 'zh'; positive = i%4 in (0,1)
    a=items_en[i%6] if lang=='en' else items_zh[i%6]
    b=items_en[(i+1)%6] if lang=='en' else items_zh[(i+1)%6]
    stocka=30+(i*7)%70; stockb=20+(i*13)%45
    threshold=stocka if positive else stocka+((i%7)+1)
    state=(f'Inventory document INV-{8000+i}: item {a} has {stocka} units; item {b} has {stockb} units. This is the complete inventory snapshot.' if lang=='en' else
           f'库存文档INV-{8000+i}：物料{a}库存为{stocka}件，物料{b}库存为{stockb}件。这是完整的库存快照。')
    ask=(f'Does the document support that item {a} has at least {threshold} units?' if lang=='en' else f'根据文档，物料{a}的库存是否不少于{threshold}件？')
    add('document_grounding',lang,state,'noul',ask,None,int(positive),meta={'fact_based':True})

pairs=[
('How do I reset my account password?', 'I cannot recall my password; how can I change it?', 'How can I delete my account permanently?', 'How do I lock my account after losing a device?'),
('Where can I download last month’s invoice?', 'How can I obtain the invoice for the previous month?', 'How do I update my billing address?', 'Where can I pay an overdue invoice?'),
('How can I track a delivered package?', 'Where do I see tracking updates for my parcel?', 'How do I change delivery priority?', 'How can I create a return label?'),
('How do I turn off auto renewal?', 'I want to stop the plan from renewing automatically.', 'How do I upgrade to a higher subscription?', 'How do I reactivate a canceled plan?'),
('How do I export the report as CSV?', 'What steps download the report as a CSV file?', 'How do I schedule the report by email?', 'How can I grant report-viewing permission?'),
('怎样关闭订阅自动续费？','如何让会员到期后不再自动续订？','怎么升级到高级会员？','取消后怎样重新开通订阅？'),
('怎样找回登录密码？','忘记密码之后如何重置？','如何永久注销账号？','怎么更新绑定的手机号？'),
('如何查看包裹物流信息？','怎样查询快递的实时运输轨迹？','怎么修改收货地址？','如何申请退换货？'),
('如何导出历史账单？','从哪里下载上一期账单文件？','怎么申请账单退款？','如何修改企业开票抬头？'),
('如何查看API调用日志？','在哪里查询接口调用记录？','怎样增加API调用配额？','如何重新签发API密钥？')]
for i in range(120):
    base=pairs[i%10]; positive=i%2==0
    lang='zh' if i%10>=5 else 'en'
    second=base[1] if positive else base[2+(i//10)%2]
    state=(f'Request A: {base[0]}\nRequest B: {second}' if lang=='en' else f'问题A：{base[0]}\n问题B：{second}')
    question=('Would substantially the same answer fully resolve both requests?' if lang=='en' else '两个问题是否能由基本相同的回答完整解决？')
    add('semantic_duplicates',lang,state,'noul',question,None,int(positive))

sent_en=[
['This purchase was terrible; I regret ordering it.','The support interaction was deeply disappointing.','I am furious about the poor product quality.','Everything went wrong and nothing was fixed.'],
['I am unhappy with delays and repeated mistakes.','The experience was frustrating overall.','The outcome disappointed me despite some effort.','The service was below expectations.'],
['The service was ordinary and met the stated basics.','It was neither particularly good nor bad.','The result was acceptable but unremarkable.','No strong positive or negative feelings about it.'],
['I am pleased with how this was handled.','The product works well and arrived as expected.','Helpful staff made the experience positive.','The service exceeded most expectations.'],
['Absolutely outstanding experience; I recommend it highly.','The support was exceptional from start to finish.','I am delighted with the flawless result.','This is the best service I have received.']]
sent_zh=[
['这次购物体验糟透了，非常后悔。','客服处理令我极度失望。','产品质量太差了，我非常愤怒。','所有环节都出了问题而且没解决。'],
['等待太久而且多次出错，我很不满意。','整个过程令人沮丧。','结果没有达到预期，比较失望。','服务整体低于我的预期。'],
['服务一般，基本满足要求。','整体既谈不上好也谈不上坏。','结果可以接受但很普通。','对此没有明显的积极或消极情绪。'],
['处理结果让我很满意。','产品运行不错，也按时送达了。','工作人员很热心，体验较好。','服务表现超过了大多数预期。'],
['体验非常出色，强烈推荐。','整个客服过程都特别优秀。','结果完美，我非常开心。','这是我经历过最好的服务。']]
score_criteria=['Very negative / 非常负面','Negative / 负面','Neutral / 中性','Positive / 正面','Very positive / 非常正面']
for level in range(5):
    for i in range(20):
        lang='en' if i%2==0 else 'zh'
        quote=(sent_en if lang=='en' else sent_zh)[level][(i//2)%4]
        state=(f'Customer feedback ({2800+i}): {quote}' if lang=='en' else f'客户反馈（{2800+i}）：{quote}')
        add('sentiment_scoring',lang,state,'score',
            'Rate the emotional valence from lowest to highest.' if lang=='en' else '从负面到正面评估情绪等级。',score_criteria,level)

urgency_criteria=['Routine / 常规','Low priority / 低优先','Medium / 中优先','High / 高优先','Critical / 严重紧急']
urgency_states=[
 ['A cosmetic icon is slightly misaligned; service works, no deadline.','页面有一个图标轻微错位，服务正常且无截止时间。'],
 ['One non-essential feature is slow for a single user; workaround available.','单个用户的非核心功能较慢，已有可用替代方案。'],
 ['A feature affecting one team is blocked, but core operations and backups still work.','一个团队的部分功能受阻，但核心流程和备份正常。'],
 ['Payment API is intermittently failing for many customers, mitigation is available but must start now.','支付API间歇性影响大量客户，可用缓解方案但必须马上处理。'],
 ['Production platform is fully unavailable for all customers, with ongoing data-loss risk.','生产平台对所有客户完全不可用，并且存在持续数据丢失风险。']]
for level in range(5):
    for i in range(20):
        lang='en' if i%2==0 else 'zh'
        sev=urgency_states[level][i%2]
        state=sev+(f' Incident ref U-{3100+i*17}.' if lang=='en' else f' 事件编号U-{3100+i*17}。')
        add('urgency_scoring',lang,state,'score',
            'Rate operational urgency from routine to critical.' if lang=='en' else '按照常规到严重紧急的顺序评估运维紧急程度。',urgency_criteria,level)

oos_opts={'search_web':'Search online information / 在线搜索',
          'read_document':'Read a local document / 阅读本地文档',
          'calculate':'Calculate numeric values / 计算数值',
          'none_of_above':'None of these tools is appropriate / 以上工具均不适用'}
oos_examples={
 'search_web':['Find the official online installation guide for a public tool.','检索公开工具的官方安装教程。'],
 'read_document':['Inspect the attached file and summarize the second page.','读取上传文件并总结第二页。'],
 'calculate':['Compute 19 percent of the listed budget amount.','计算预算金额的19%。'],
 'none_of_above':['Please send an email to the team immediately.','请立即向团队发送邮件。','Book a meeting on my calendar for tomorrow.','在日历上预约明天的会议。','Analyze the attached photo visually.','分析附件图片内容。','Deploy my service using remote shell commands.','通过远程命令部署我的服务。']
}
for i in range(100):
    lang='en' if i%2==0 else 'zh'
    if i<50:
        label=['search_web','read_document','calculate'][i%3]
        raw=oos_examples[label][0 if lang=='en' else 1]
    else:
        label='none_of_above'
        raw=oos_examples[label][((i//2)%4)*2 + (0 if lang=='en' else 1)]
    add('out_of_scope',lang,vary(raw,i,lang),'choice',
        'Select the appropriate available tool; use none_of_above if none can do the task.' if lang=='en' else '请选择能执行任务的工具；若都不适合则选择none_of_above。',
        oos_opts,label)

# Paired reorder challenge. Mirror 40 support + 40 routing samples.
for category in ['customer_support','tool_routing']:
    base=[x for x in cases if x['category']==category]
    sampled=R.sample(base,40)
    for src in sampled:
        src['metadata']['permutation_group']=src['id']
        opts=list(src['question']['criteria'].items())
        R.shuffle(opts)
        # ensure order changes when possible
        if opts==list(src['question']['criteria'].items()): opts=opts[1:]+opts[:1]
        add('option_order_robustness',src['language'],src['state'],'choice',
            src['question']['instructions'],dict(opts),src['gold'],
            meta={'permutation_group':src['id'],'original_category':category,'paired_id':src['id']})

assert len(cases)==1300,len(cases)
assert len({r['id'] for r in cases})==1300
assert sum(r['question']['type']=='noul' for r in cases)==240
assert sum(r['question']['type']=='score' for r in cases)==200
assert sum(r['question']['type']=='choice' for r in cases)==860

jsonl=OUT/'clm_jev_1300.jsonl'
with jsonl.open('w',encoding='utf-8') as fp:
    for row in cases:
        fp.write(json.dumps(row,ensure_ascii=False)+'\n')

csv_path=OUT/'clm_jev_1300.csv'
with csv_path.open('w',newline='',encoding='utf-8-sig') as fp:
    wr=csv.writer(fp)
    wr.writerow(['id','category','language','question_type','state','instructions','criteria_json','gold','gold_source','metadata_json'])
    for x in cases:
        wr.writerow([x['id'],x['category'],x['language'],x['question']['type'],x['state'],x['question']['instructions'],
                     json.dumps(x['question'].get('criteria'),ensure_ascii=False),x['gold'],x['gold_source'],json.dumps(x['metadata'],ensure_ascii=False)])

manifest={'dataset':'CLM-JEV Synthetic Decision Benchmark','version':'1.0','created':'2026-10-13',
 'count':len(cases),'gold_source':'synthetic rule/template; not independently human-reviewed',
 'comparability':'Exact same input, options, ground truth for all models',
 'categories':dict(sorted(Counter(x['category'] for x in cases).items())),
 'question_types':dict(Counter(x['question']['type'] for x in cases)),
 'languages':dict(Counter(x['language'] for x in cases)),
 'caveats':['Synthetic template examples may be easier than real production data.',
 'Labels reflect author rules and should not be represented as human annotations.',
 'Paired reorder samples should be reported separately to avoid inflating independence.',
 'Strong research conclusions require an independent public or expert-labelled test set.'],
 'sources':{'this_dataset':'Original generated templates; not copied from online data',
           'public_companion':'https://github.com/Running-Dolphins/jev-bench',
           'clm':'https://github.com/Contrastive-LM/CLM',
           'jev':'https://docs.typesafe.ai/introduction'}}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False,indent=2))
