"""Kids HQ — Barefoot jars, family bonus, principles and Mattermost copy.

Pure functions. No Flask, no SQLite. Arithmetic and words for Maia, Annaliese and TJ.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

JARS = ('splurge', 'smile', 'give', 'grow')

DEFAULT_SETTINGS = {
    'enabled': True,
    'timezone': 'Australia/Brisbane',
    'money_meal_weekday': 6,  # Sunday
    'money_meal_hour': 16,
    'bonus_rate_weekly': 0.01,
    'bonus_cap': 2.0,
    'family_finance_channel_id': '',
}

DEFAULT_CHILDREN = [
    {
        'key': 'maia',
        'name': 'Maia',
        'age': 12,
        'pin_hash': '',
        'mattermost_username': '',
        'mattermost_channel_id': '',
        'jars': ['splurge', 'smile', 'give', 'grow'],
        'split': {'splurge': 0.4, 'smile': 0.4, 'give': 0.1, 'grow': 0.1},
        'smile_floor': 0.3,
        'lesson_pay': 3.0,
        'seed': 100.0,
        'seed_jar': 'grow',
        'split_locked': False,
    },
    {
        'key': 'annaliese',
        'name': 'Annaliese',
        'age': 8,
        'pin_hash': '',
        'mattermost_username': '',
        'mattermost_channel_id': '',
        'jars': ['splurge', 'smile', 'give'],
        'split': {'splurge': 0.4, 'smile': 0.5, 'give': 0.1},
        'smile_floor': 0.3,
        'lesson_pay': 2.0,
        'seed': 100.0,
        'seed_jar': 'smile',
        'split_locked': False,
    },
    {
        'key': 'tj',
        'name': 'TJ',
        'age': 6,
        'pin_hash': '',
        'mattermost_username': '',
        'mattermost_channel_id': '',
        'jars': ['splurge', 'smile', 'give'],
        'split': {'splurge': 0.4, 'smile': 0.5, 'give': 0.1},
        'smile_floor': 0.5,
        'lesson_pay': 1.0,
        'seed': 100.0,
        'seed_jar': 'smile',
        'split_locked': True,
    },
]

# Age bands match Kit playbooks: 5–7, 8–10, 11–13.
PRINCIPLES = [
    # TJ (6)
    {
        'id': 'coins',
        'ages': (5, 7),
        'title': 'Coins have value',
        'lesson': 'The 50-cent coin is the biggest, but $2 is worth more. Size is not value.',
        'game': 'coin-sort',
        'question': 'Which is worth the most?',
        'options': ['The 50-cent coin (it is biggest)', 'A $2 coin', 'A 5-cent coin'],
        'answer': 1,
    },
    {
        'id': 'need-want',
        'ages': (5, 7),
        'title': 'Need or want?',
        'lesson': 'Needs keep us going (food, water, a bed). Wants are nice (lollies, stickers). Needs come first.',
        'game': 'need-want',
        'question': 'Drinking water is a…',
        'options': ['Want', 'Need', 'Treat'],
        'answer': 1,
    },
    {
        'id': 'family-vs-extra',
        'ages': (5, 7),
        'title': 'Family jobs are free',
        'lesson': 'You help at home because you live here. Extra jobs (washing the car, a big tidy) earn extra money.',
        'game': 'family-vs-extra',
        'question': 'Making your bed is…',
        'options': ['An extra job you get paid for', 'A family job — you do it because you live here', 'Optional'],
        'answer': 1,
    },
    {
        'id': 'magic-jar',
        'ages': (5, 7),
        'title': 'The magic jar',
        'lesson': 'Money you leave in Smile can have a baby. That extra is called interest. Money you spend cannot grow.',
        'game': 'magic-jar',
        'question': 'If you leave $10 in Smile, next week it can be…',
        'options': ['Gone', 'A bit more, because it stayed', 'Only $10 forever'],
        'answer': 1,
    },
    {
        'id': 'digital-money',
        'ages': (5, 7),
        'title': 'The card still spends real money',
        'lesson': 'Tapping the Kit card is the same as handing over coins. The number on Splurge goes down.',
        'game': 'digital-money',
        'question': 'After you tap the card for a $3 ice block, Splurge…',
        'options': ['Stays the same', 'Goes down by $3', 'Goes up'],
        'answer': 1,
    },
    {
        'id': 'lost-card',
        'ages': (5, 7),
        'title': 'PIN is a secret',
        'lesson': 'Never tell a friend your PIN. If someone asks, say no and tell Mum or Dad.',
        'game': 'lost-card',
        'question': 'A friend asks for your Kit PIN. You…',
        'options': ['Tell them, they are a friend', 'Say no and tell Mum or Dad', 'Write it on the card'],
        'answer': 1,
    },
    {
        'id': 'wait-sunday',
        'ages': (5, 7),
        'title': 'Waiting until Sunday',
        'lesson': 'Extra jobs are paid at the Money Meal, not the second you tick them. Waiting is a muscle.',
        'game': 'wait-sunday',
        'question': 'You finished an extra job on Wednesday. You get paid…',
        'options': ['Straight away', 'At Sunday Money Meal, if Mum or Dad say yes', 'Whenever you ask'],
        'answer': 1,
    },
    {
        'id': 'give-tj',
        'ages': (5, 7),
        'title': 'Make someone smile',
        'lesson': 'A bit of every extra-job pay goes in Give. It is for other people, not for more toys.',
        'game': 'give',
        'question': 'Give money is for…',
        'options': ['More lollies', 'Helping someone else', 'The card'],
        'answer': 1,
    },
    # Annaliese (8)
    {
        'id': 'split-pay',
        'ages': (8, 10),
        'title': 'Split the pay',
        'lesson': 'Save before you spend. When extra-job money lands, some goes Splurge, more goes Smile, a bit goes Give.',
        'game': 'split-pay',
        'question': 'The Barefoot rule is…',
        'options': ['Spend first, save what is left', 'Save first, spend what is left', 'Put it all on the card'],
        'answer': 1,
    },
    {
        'id': 'interest-garden',
        'ages': (8, 10),
        'title': 'Interest is a reward for leaving it',
        'lesson': 'The bank (and the Bank of Mum and Dad) pay extra on money that stays in Smile or Grow. Spend it and the extra stops.',
        'game': 'interest-garden',
        'question': 'Family bonus is paid on…',
        'options': ['Splurge, because you spent it', 'Smile and Grow, because you left it', 'Give only'],
        'answer': 1,
    },
    {
        'id': 'sleep-on-it',
        'ages': (8, 10),
        'title': 'Sleep on it',
        'lesson': 'If you want a thing, put it on the wish list and wait until next Sunday. Nagging does not turn a no into a yes.',
        'game': 'shop-trap',
        'question': 'You want something at the shops today. The house rule is…',
        'options': ['Buy it if Splurge has enough', 'Sleep on it until the next Money Meal, unless Mum or Dad already said yes', 'Ask until they give in'],
        'answer': 1,
    },
    {
        'id': 'smile-goal',
        'ages': (8, 10),
        'title': 'One Smile goal',
        'lesson': 'Pick one thing Mum or Dad approve. Know the price. Watch the bar fill. Short goals first — weeks, then a couple of months.',
        'game': 'smile-goal',
        'question': 'A good Smile goal is…',
        'options': ['Whatever you saw in an ad just now', 'One thing you really want, priced, that Mum or Dad approve', 'Everything on a list'],
        'answer': 1,
    },
    {
        'id': 'second-hand',
        'ages': (8, 10),
        'title': 'One person’s trash is another’s treasure',
        'lesson': 'Second-hand can get you the same smile for less, so Smile fills the goal faster.',
        'game': 'second-hand',
        'question': 'A $40 game is $18 second-hand and Mum or Dad say it is fine. Smart money…',
        'options': ['Always buys new', 'Takes the $18 path if it is what you wanted', 'Waits forever'],
        'answer': 1,
    },
    {
        'id': 'ads',
        'ages': (8, 10),
        'title': 'Ads are trying to get you',
        'lesson': 'Flash sales and “everyone has it” are tricks. Pause. Is it a need, a want, or an ad?',
        'game': 'ads',
        'question': 'A game says “only 5 minutes left on this skin”. That is…',
        'options': ['A fact you must obey', 'A trick to make you tap now', 'A need'],
        'answer': 1,
    },
    {
        'id': 'no-bailouts',
        'ages': (8, 10),
        'title': 'No bailouts',
        'lesson': 'If Splurge is empty on Friday, you wait until Sunday’s extra jobs. We do not top you up, and you do not raid Smile.',
        'game': 'no-bailouts',
        'question': 'Splurge is $0 and you want snacks with friends. You…',
        'options': ['Raid Smile', 'Ask Mum to top up the card', 'Wait, and plan better next week'],
        'answer': 2,
    },
    {
        'id': 'give-annaliese',
        'ages': (8, 10),
        'title': 'Give is the brat-buster',
        'lesson': 'The happiest people give. Pick a real Give this term — a person, a cause, or something kind at home.',
        'game': 'give',
        'question': 'Give money is best used to…',
        'options': ['Buy yourself a backup toy', 'Help someone else on purpose', 'Fill Splurge'],
        'answer': 1,
    },
    # Maia (12)
    {
        'id': 'save-first',
        'ages': (11, 13),
        'title': 'Spend what is left after saving',
        'lesson': 'Do not save what is left after spending. Split extra-job pay the moment it lands. Smile has a floor of 30%.',
        'game': 'save-first',
        'question': 'If extra-job pay is $10, the first job is…',
        'options': ['See what is left after Splurge', 'Put Smile (and Grow) aside, then Splurge', 'Leave it all on the card'],
        'answer': 1,
    },
    {
        'id': 'compounding',
        'ages': (11, 13),
        'title': 'Time is the hidden ingredient',
        'lesson': 'Interest earns interest. Starting early beats being clever. The Rule of 72: 72 ÷ rate ≈ years to double.',
        'game': 'time-machine',
        'question': 'At about 6% a year, money doubles in roughly…',
        'options': ['6 years', '12 years', '72 years'],
        'answer': 1,
    },
    {
        'id': 'opportunity-cost',
        'ages': (11, 13),
        'title': 'Every yes is a no to something else',
        'lesson': 'Spend $40 on a want today and you also spend the future extra that $40 would have earned in Grow.',
        'game': 'opportunity-cost',
        'question': 'Opportunity cost is…',
        'options': ['The sticker price', 'The thing you gave up, including future growth', 'GST'],
        'answer': 1,
    },
    {
        'id': 'inflation',
        'ages': (11, 13),
        'title': 'The inflation monster',
        'lesson': 'Prices creep up. $20 today may not buy the same thing next year. Saving still wins if you start now, because growth can outrun the creep.',
        'game': 'inflation',
        'question': 'If a $20 thing becomes $22 next year, that is…',
        'options': ['Interest', 'Inflation', 'A scam'],
        'answer': 1,
    },
    {
        'id': 'debit-credit',
        'ages': (11, 13),
        'title': 'Debit vs credit',
        'lesson': 'Debit is your money. Credit is someone else’s, with a price. Interest on debt grows against you. Barefoot: never get a credit card as a life rule when you are older.',
        'game': 'debit-credit',
        'question': 'A Kit tap spends…',
        'options': ['The bank’s money, to pay back later', 'Your Splurge, now', 'Youthsaver'],
        'answer': 1,
    },
    {
        'id': 'value',
        'ages': (11, 13),
        'title': 'Price is what you pay, value is what you get',
        'lesson': 'Cheap is not always good value. Quality that lasts can cost more now and less later. A bargain is not a bargain unless you needed it.',
        'game': 'value',
        'question': 'A $8 shirt that lasts one wash versus a $20 shirt that lasts two years. Better value is often…',
        'options': ['Always the $8 one', 'The one that does the job for longer per dollar, if you needed a shirt', 'The one on sale regardless'],
        'answer': 1,
    },
    {
        'id': 'barefoot-boss',
        'ages': (11, 13),
        'title': 'Be a Barefoot boss',
        'lesson': 'Extra jobs and a tiny business (a stall, second-hand books, a skill) earn extra. Write: what, who it is for, costs, price, profit.',
        'game': 'barefoot-boss',
        'question': 'Profit is…',
        'options': ['The price you charge', 'Price minus what it cost you', 'Pocket money'],
        'answer': 1,
    },
    {
        'id': 'give-maia',
        'ages': (11, 13),
        'title': 'Give with your values',
        'lesson': 'Give on purpose, without emptying Smile. Kindness counts. Charity begins at home and does not stop there.',
        'game': 'give',
        'question': 'A healthy Give habit…',
        'options': ['Wipes out Smile whenever you feel guilty', 'Is a planned slice of extra-job pay', 'Is only at Christmas'],
        'answer': 1,
    },
    {
        'id': 'scams',
        'ages': (11, 13),
        'title': 'If it sounds like free money, it is not',
        'lesson': 'Scams disguise themselves as prizes, friends in a hurry, or get-rich-quick. Stop, pause, ask an adult. Never share PINs, passwords or codes.',
        'game': 'scams',
        'question': 'A message says you won $500, click to claim. You…',
        'options': ['Click fast before it expires', 'Stop, do not tap, tell Mum or Dad', 'Reply with your PIN to prove it is you'],
        'answer': 1,
    },
]


def round_cents(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def empty_jars() -> dict[str, float]:
    return {jar: 0.0 for jar in JARS}


def kids_settings(config: dict | None) -> dict:
    """Documented defaults overlaid by config['kids']. Children merge by key."""
    settings = dict(DEFAULT_SETTINGS)
    configured = (config or {}).get('kids') or {}
    for key, value in configured.items():
        if key == 'children':
            continue
        settings[key] = value
    by_key = {child['key']: dict(child) for child in DEFAULT_CHILDREN}
    for child in configured.get('children') or []:
        key = child.get('key')
        if not key:
            continue
        merged = by_key.get(key, {'key': key})
        merged.update(child)
        if 'jars' in child:
            merged['jars'] = list(child['jars'])
        if 'split' in child:
            merged['split'] = dict(child['split'])
        by_key[key] = merged
    order = [c['key'] for c in DEFAULT_CHILDREN]
    extras = [k for k in by_key if k not in order]
    settings['children'] = [by_key[k] for k in order if k in by_key] + [by_key[k] for k in extras]
    return settings


def child_by_key(settings: dict, key: str) -> dict | None:
    key = (key or '').strip().lower()
    for child in settings.get('children') or []:
        if child.get('key') == key:
            return child
    return None


def hash_pin(pin: str) -> str:
    pin = str(pin or '').strip()
    if not re.fullmatch(r'\d{4}', pin):
        raise ValueError('PIN must be four digits')
    return generate_password_hash(pin)


def pin_ok(pin: str, pin_hash: str) -> bool:
    pin = str(pin or '').strip()
    pin_hash = str(pin_hash or '').strip()
    if not pin_hash or not re.fullmatch(r'\d{4}', pin):
        return False
    try:
        return check_password_hash(pin_hash, pin)
    except (ValueError, TypeError):
        return False


def child_jars(child: dict) -> list[str]:
    names = list(child.get('jars') or ['splurge', 'smile', 'give'])
    return [j for j in names if j in JARS]


def split_pay(amount: float, child: dict) -> dict[str, float]:
    """Split extra-job or lesson pay across the child's jars. Remainder goes to the last jar."""
    amount = round_cents(amount)
    if amount <= 0:
        return {jar: 0.0 for jar in child_jars(child)}
    split = child.get('split') or {}
    ordered = [jar for jar in child_jars(child) if jar in split]
    if not ordered:
        ordered = child_jars(child)
        split = {ordered[-1]: 1.0}
    parts = {jar: 0.0 for jar in child_jars(child)}
    allocated = 0.0
    for i, jar in enumerate(ordered):
        if i == len(ordered) - 1:
            parts[jar] = round_cents(amount - allocated)
        else:
            parts[jar] = round_cents(amount * float(split.get(jar) or 0))
            allocated = round_cents(allocated + parts[jar])
    return parts


def family_bonus(smile: float, grow: float, rate: float = 0.01, cap: float = 2.0) -> float:
    base = max(0.0, float(smile or 0)) + max(0.0, float(grow or 0))
    if base <= 0 or rate <= 0:
        return 0.0
    return min(round_cents(base * float(rate)), round_cents(cap))


def bonus_jar(child: dict) -> str:
    return 'grow' if 'grow' in child_jars(child) else 'smile'


def credit_jars(jars: dict, parts: dict) -> dict[str, float]:
    out = empty_jars()
    out.update({jar: round_cents(jars.get(jar) or 0) for jar in JARS})
    for jar, amount in (parts or {}).items():
        if jar in out:
            out[jar] = round_cents(out[jar] + float(amount or 0))
    return out


def jars_from_row(row: dict | None, child: dict | None = None) -> dict[str, float]:
    jars = empty_jars()
    if not row:
        return jars
    for jar in JARS:
        jars[jar] = round_cents(row.get(jar) or 0)
    return jars


def dollars(value: float) -> str:
    return f'${value:,.2f}'


def lesson_pay_for(child: dict) -> float:
    return round_cents(child.get('lesson_pay') if child.get('lesson_pay') is not None else 1)


def principle_by_id(pid: str) -> dict | None:
    for item in PRINCIPLES:
        if item['id'] == pid:
            return item
    return None


def in_age_band(age: int, band: tuple[int, int]) -> bool:
    return band[0] <= int(age) <= band[1]


def principles_for(child: dict) -> list[dict]:
    age = int(child.get('age') or 0)
    return [p for p in PRINCIPLES if in_age_band(age, p['ages'])]


def next_principle(child: dict, completed_ids) -> dict | None:
    done = set(completed_ids or [])
    for item in principles_for(child):
        if item['id'] not in done:
            return item
    return None


def parse_kid_command(text: str) -> dict | None:
    words = ' '.join(str(text or '').lower().split())
    if words in ('jars', 'balance', 'balances', 'jar'):
        return {'action': 'jars'}
    if words in ('goal', 'smile', 'goals'):
        return {'action': 'goal'}
    if words in ('help', '?'):
        return {'action': 'help'}
    return None


_PARENT_JARS_RE = re.compile(
    r'^\s*jars\s+([a-z]+)\s+'
    r'(?:splurge\s+)?\$?\s*(\d+(?:\.\d{1,2})?)\s+'
    r'(?:smile\s+)?\$?\s*(\d+(?:\.\d{1,2})?)\s+'
    r'(?:give\s+)?\$?\s*(\d+(?:\.\d{1,2})?)'
    r'(?:\s+(?:grow\s+)?\$?\s*(\d+(?:\.\d{1,2})?))?\s*$',
    re.I,
)


def parse_parent_jars(text: str, child: dict | None = None) -> dict | None:
    """Parse 'jars maia 12.40 48 6.20 101' or 'jars maia splurge 12.40 smile 48 give 6.20 grow 101'."""
    match = _PARENT_JARS_RE.match(str(text or ''))
    if not match:
        return None
    key = match.group(1).lower()
    if child and key != child.get('key'):
        return None
    jars = empty_jars()
    jars['splurge'] = round_cents(match.group(2))
    jars['smile'] = round_cents(match.group(3))
    jars['give'] = round_cents(match.group(4))
    if match.group(5) is not None:
        jars['grow'] = round_cents(match.group(5))
    return {'action': 'set_jars', 'key': key, 'jars': jars}


def posting_allowed(channel_id: str, child: dict, family_finance_channel_id: str) -> bool:
    channel_id = (channel_id or '').strip()
    child_channel = str(child.get('mattermost_channel_id') or '').strip()
    family = (family_finance_channel_id or '').strip()
    if not channel_id or not child_channel:
        return False
    if family and channel_id == family:
        return False
    return channel_id == child_channel


def compose_kid_help() -> str:
    return 'You can type: jars · goal · help'


def compose_jars_line(child: dict, jars: dict) -> str:
    bits = [f'{jar.title()} {dollars(jars.get(jar) or 0)}' for jar in child_jars(child)]
    return ' · '.join(bits)


def compose_goal_line(goal: dict | None, smile: float) -> str:
    if not goal:
        return 'No Smile goal yet. Pick one thing Mum or Dad approve.'
    target = round_cents(goal.get('target_amount') or 0)
    title = goal.get('title') or 'Smile goal'
    if target <= 0:
        return f'Smile goal: {title}.'
    left = round_cents(target - smile)
    if left <= 0:
        return f'Smile goal: {title} {dollars(target)} — you are there. Nice.'
    return f'Smile goal: {title} {dollars(target)} — {dollars(left)} to go.'


def compose_money_meal(child: dict, jars: dict, bonus: float, goal: dict | None,
                       next_item: dict | None, pay_queue: list | None = None) -> str:
    name = child.get('name') or child.get('key') or 'you'
    lines = [
        f'Hi {name}.',
        '',
        compose_jars_line(child, jars),
        '',
    ]
    if bonus > 0:
        jar = bonus_jar(child).title()
        lines.append(
            f'Family bonus this week: {dollars(bonus)} because you left money in Smile'
            + (' and Grow.' if bonus_jar(child) == 'grow' else '.')
            + f' It goes into {jar}.'
        )
    else:
        lines.append('No family bonus this week — Smile and Grow were empty, or already paid.')
    lines.append('')
    lines.append(compose_goal_line(goal, jars.get('smile') or 0))
    if next_item:
        lines.append(f'Next principle: {next_item["title"]}.')
    else:
        lines.append('You have finished this term’s principles. Play them again for fun — no extra pay.')
    lines += ['', 'Money Meal is at 4pm.']
    due = [item for item in (pay_queue or []) if item.get('child_key') == child.get('key')]
    if due:
        lines.append('')
        lines.append('Mum and Dad still need to put this in Kit:')
        for item in due:
            lines.append(f'- {dollars(item["amount"])} {item.get("note") or item.get("kind")}')
    return '\n'.join(lines)


def compose_parent_digest(pay_queue: list, children: list | None = None) -> str:
    if not pay_queue:
        return 'Kids HQ: nothing to put in Kit this week.'
    names = {c['key']: c.get('name') or c['key'] for c in (children or [])}
    lines = ['Kids HQ — pay into Kit', '']
    for item in pay_queue:
        who = names.get(item.get('child_key'), item.get('child_key'))
        lines.append(f'- {who}: {dollars(item["amount"])} — {item.get("note") or item.get("kind")}')
    lines.append('')
    lines.append('When it is in Kit, tick it on the Kids page (or type paid kit <name>).')
    return '\n'.join(lines)


def decide_on_date(today: date) -> date:
    """Next Sunday after today (the following Money Meal). If today is Sunday, the one after."""
    days = (6 - today.weekday()) % 7 or 7
    return today + timedelta(days=days)
