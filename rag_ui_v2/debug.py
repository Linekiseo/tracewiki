from pathlib import Path
from playwright.sync_api import sync_playwright
html=Path('/mnt/data/rag_ui_v2/index.html').read_text().replace('<head>',"<head><script>window.PAGE_OVERRIDE='iteration'</script>",1)
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 page=b.new_page(viewport={'width':1400,'height':900})
 page.on('console',lambda msg: print('CONSOLE',msg.type,msg.text))
 page.on('pageerror',lambda exc: print('PAGEERROR',exc))
 page.set_content(html,wait_until='load')
 page.wait_for_timeout(500)
 print('nav',page.locator('#nav').inner_html())
 print('page len',len(page.locator('#page').inner_html()))
 b.close()
