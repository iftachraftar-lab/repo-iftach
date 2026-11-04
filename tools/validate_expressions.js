const fs = require('fs');
const html = fs.readFileSync('static/index.html', 'utf8');
const attrRegex = /(x-[a-zA-Z0-9\.\-:]+|@[a-zA-Z0-9\.\-:]+|:[a-zA-Z0-9\.\-:]+)="([^"]*)"/g;
let match;
let count = 0;
let errors = 0;
while ((match = attrRegex.exec(html)) !== null) {
  count++;
  const [_, attr, expr] = match;
  try {
    if (attr.startsWith('@') || attr.startsWith('x-on:')) {
      new Function('state', 'with(state){ ' + expr + ' }');
    } else if (attr === ':class' || attr === 'x-show' || attr === 'x-if' || attr === ':disabled' || attr === ':style') {
      new Function('state', 'with(state){ return (' + expr + '); }');
    }
  } catch(err) {
    console.error('Syntax error in', attr, ':', expr, '-->', err.message);
    errors++;
  }
}
console.log('Validated', count, 'attributes. Errors found:', errors);
if (errors > 0) process.exit(1);
