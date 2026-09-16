from pathlib import Path
from playwright.sync_api import sync_playwright

pages = [
    ('01_项目工作台','dashboard'),('02_研究主题','research'),('03_研究迭代联动工作台','iteration'),
    ('04_全局检索','search'),('05_证据链详情','evidence'),('06_关联图谱','graph'),
    ('07_Codex会话列表','sessions'),('08_Codex会话详情','session-detail'),('09_代码仓库浏览器','repo'),
    ('10_代码变更与绑定','changeset'),('11_实验中心','experiments'),('12_实验对照详情','run-compare'),
    ('13_科研文档与Claim','documents'),('14_Claim验证','claim'),('15_版本漂移分析','drift'),
    ('16_Binding复核中心','bindings'),('17_数据接入与索引运维','ingestion'),('18_评测与观测','evaluation'),
    ('19_权限与审计','audit'),('20_项目设置','settings')
]
root=Path('/mnt/data/rag_ui_v2')
html=(root/'index.html').read_text()
outdir=root/'screens'; outdir.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    for name,key in pages:
        context=browser.new_context(viewport={'width':1800,'height':1125}, device_scale_factor=1)
        page=context.new_page()
        content=html.replace('<head>', f"<head><script>window.PAGE_OVERRIDE='{key}'</script>", 1)
        page.set_content(content, wait_until='load')
        page.wait_for_timeout(250)
        page.screenshot(path=str(outdir/f'{name}.png'), full_page=False)
        context.close()
        print(name)
    browser.close()
