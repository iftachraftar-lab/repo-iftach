const puppeteer = require('C:/Users/Iftach Raf/node_modules/puppeteer-core');
const path = require('path');

(async () => {
    try {
        const browser = await puppeteer.launch({
            executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
            headless: true
        });
        const page = await browser.newPage();
        const filePath = 'file:///' + path.resolve('static/index.html').replace(/\\/g, '/');
        await page.goto(filePath);
        await page.evaluate(() => {
            localStorage.clear();
        });
        await page.reload({ waitUntil: 'networkidle2' });

        const buttonsInfo = await page.evaluate(() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const matching = buttons.filter(b => b.innerHTML.includes('install_mobile') || b.textContent.includes('הורד'));
            return matching.map(btn => {
                const style = window.getComputedStyle(btn);
                return {
                    text: btn.textContent.trim(),
                    display: style.display,
                    visibility: style.visibility,
                    offsetParent: !!btn.offsetParent,
                    parentTag: btn.parentElement?.tagName
                };
            });
        });
        console.log('When logged out (Login Gate):', JSON.stringify(buttonsInfo, null, 2));

        await browser.close();
    } catch (e) {
        console.error('Test error:', e);
    }
})();
