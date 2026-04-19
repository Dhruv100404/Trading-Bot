const buy = {
'2025-12-29':{pnl:292},'2025-12-30':{pnl:-43},'2025-12-31':{pnl:510},
'2026-01-01':{pnl:-373},'2026-01-02':{pnl:432},'2026-01-05':{pnl:-514},
'2026-01-06':{pnl:164},'2026-01-07':{pnl:292},'2026-01-08':{pnl:-2336},
'2026-01-09':{pnl:-2796},'2026-01-12':{pnl:-502},'2026-01-13':{pnl:-1692},
'2026-01-14':{pnl:1441},'2026-01-16':{pnl:-935},'2026-01-19':{pnl:-1120},
'2026-01-20':{pnl:-1438},'2026-01-21':{pnl:-691},'2026-01-22':{pnl:577},
'2026-01-23':{pnl:147},'2026-01-27':{pnl:3},'2026-01-28':{pnl:377},
'2026-01-29':{pnl:651},'2026-01-30':{pnl:602},'2026-02-02':{pnl:518},
'2026-02-03':{pnl:-787},'2026-02-04':{pnl:1223},'2026-02-05':{pnl:-1136},
'2026-02-06':{pnl:-960},'2026-02-09':{pnl:507},'2026-02-10':{pnl:-325},
'2026-02-11':{pnl:71},'2026-02-12':{pnl:156},'2026-02-13':{pnl:-248},
'2026-02-16':{pnl:1235},'2026-02-17':{pnl:88},'2026-02-18':{pnl:538},
'2026-02-19':{pnl:-707},'2026-02-20':{pnl:232},'2026-02-23':{pnl:371},
'2026-02-24':{pnl:-416},'2026-02-25':{pnl:-409},'2026-02-26':{pnl:-617},
'2026-02-27':{pnl:-237},'2026-03-02':{pnl:1447},'2026-03-04':{pnl:-973},
'2026-03-05':{pnl:637},'2026-03-06':{pnl:2385},'2026-03-09':{pnl:-69},
'2026-03-10':{pnl:-36},'2026-03-11':{pnl:409},'2026-03-12':{pnl:154},
'2026-03-13':{pnl:25},'2026-03-16':{pnl:189},'2026-03-17':{pnl:65},
'2026-03-18':{pnl:1605},'2026-03-19':{pnl:224},'2026-03-20':{pnl:3211},
'2026-03-23':{pnl:-575},'2026-03-24':{pnl:320},
}
const sell = {
'2025-12-29':{pnl:333},'2025-12-30':{pnl:244},'2025-12-31':{pnl:161},
'2026-01-01':{pnl:-169},'2026-01-02':{pnl:-46},'2026-01-05':{pnl:931},
'2026-01-06':{pnl:260},'2026-01-07':{pnl:4},'2026-01-08':{pnl:963},
'2026-01-09':{pnl:364},'2026-01-12':{pnl:969},'2026-01-13':{pnl:872},
'2026-01-14':{pnl:-169},'2026-01-16':{pnl:195},'2026-01-19':{pnl:-169},
'2026-01-20':{pnl:2062},'2026-01-21':{pnl:96},'2026-01-22':{pnl:-503},
'2026-01-23':{pnl:-275},'2026-01-27':{pnl:-14},'2026-01-28':{pnl:-81},
'2026-01-29':{pnl:-561},'2026-01-30':{pnl:-321},'2026-02-02':{pnl:-1031},
'2026-02-03':{pnl:1636},'2026-02-04':{pnl:-234},'2026-02-05':{pnl:255},
'2026-02-06':{pnl:697},'2026-02-09':{pnl:119},'2026-02-10':{pnl:-142},
'2026-02-11':{pnl:592},'2026-02-12':{pnl:-47},'2026-02-13':{pnl:1741},
'2026-02-16':{pnl:-643},'2026-02-17':{pnl:193},'2026-02-18':{pnl:29},
'2026-02-19':{pnl:174},'2026-02-20':{pnl:-1007},'2026-02-23':{pnl:370},
'2026-02-24':{pnl:1213},'2026-02-25':{pnl:256},'2026-02-26':{pnl:-102},
'2026-02-27':{pnl:-161},'2026-03-02':{pnl:-1083},'2026-03-04':{pnl:2553},
'2026-03-05':{pnl:660},'2026-03-06':{pnl:-364},'2026-03-09':{pnl:84},
'2026-03-10':{pnl:1885},'2026-03-11':{pnl:-139},'2026-03-12':{pnl:-489},
'2026-03-13':{pnl:1154},'2026-03-16':{pnl:-1362},'2026-03-17':{pnl:499},
'2026-03-19':{pnl:-1793},'2026-03-20':{pnl:-123},'2026-03-23':{pnl:444},
'2026-03-24':{pnl:-353},
}
const regime = {
'2025-12-29':'flat_up','2025-12-30':'flat_down','2025-12-31':'flat_up',
'2026-01-01':'flat_down','2026-01-02':'flat_up','2026-01-05':'gap_down',
'2026-01-06':'flat_down','2026-01-07':'flat_up','2026-01-08':'gap_down',
'2026-01-09':'flat_down','2026-01-12':'gap_down','2026-01-13':'gap_down',
'2026-01-14':'flat_up','2026-01-16':'flat_up','2026-01-19':'flat_down',
'2026-01-20':'gap_down','2026-01-21':'flat_down','2026-01-22':'gap_up',
'2026-01-23':'flat_up','2026-01-27':'flat_up','2026-01-28':'flat_up',
'2026-01-29':'flat_up','2026-01-30':'flat_down','2026-02-02':'gap_down',
'2026-02-03':'gap_up','2026-02-04':'flat_down','2026-02-05':'flat_down',
'2026-02-06':'flat_down','2026-02-09':'flat_up','2026-02-10':'flat_up',
'2026-02-11':'flat_up','2026-02-12':'flat_down','2026-02-13':'flat_down',
'2026-02-16':'flat_down','2026-02-17':'flat_down','2026-02-18':'flat_up',
'2026-02-19':'flat_down','2026-02-20':'flat_up','2026-02-23':'flat_up',
'2026-02-24':'flat_down','2026-02-25':'flat_up','2026-02-26':'flat_up',
'2026-02-27':'flat_up','2026-03-02':'gap_down','2026-03-04':'gap_down',
'2026-03-05':'flat_up','2026-03-06':'flat_down','2026-03-09':'gap_down',
'2026-03-10':'gap_up','2026-03-11':'flat_up','2026-03-12':'flat_down',
'2026-03-13':'flat_down','2026-03-16':'flat_down','2026-03-17':'flat_up',
'2026-03-18':'flat_up','2026-03-19':'gap_down','2026-03-20':'flat_up',
'2026-03-23':'gap_down','2026-03-24':'gap_up',
}
const dates = Object.keys(buy).sort()
let a_total=0,a_green=0,a_red=0
let b_total=0,b_green=0,b_red=0
let c_total=0,c_green=0,c_red=0
for (const d of dates) {
    const r = regime[d], bp = buy[d]?.pnl ?? 0, sp = sell[d]?.pnl ?? 0
    a_total += bp; if (bp > 0) a_green++; else a_red++
    if (r === 'gap_up') {
        b_total += sp; if (sp > 0) b_green++; else b_red++
    } else {
        b_total += bp; if (bp > 0) b_green++; else b_red++
    }
    if (r !== 'gap_up') { c_total += bp; if (bp > 0) c_green++; else c_red++ }
}
console.log('=== 59 TRADING DAYS COMPARISON ===')
console.log('A: BUY ONLY (all days)        | P&L:', Math.round(a_total), '| Green:', a_green, 'Red:', a_red, '(' + (a_green/(a_green+a_red)*100).toFixed(0) + '%)')
console.log('B: REGIME (BUY+SELL on gap_up) | P&L:', Math.round(b_total), '| Green:', b_green, 'Red:', b_red, '(' + (b_green/(b_green+b_red)*100).toFixed(0) + '%)')
console.log('C: BUY ONLY skip gap_up days   | P&L:', Math.round(c_total), '| Green:', c_green, 'Red:', c_red, '(' + (c_green/(c_green+c_red)*100).toFixed(0) + '%)')
console.log()
console.log('B vs A: extra P&L =', Math.round(b_total - a_total))
console.log()
console.log('=== GAP-UP DAYS (regime filter applies here) ===')
const gu = dates.filter(d => regime[d] === 'gap_up')
for (const d of gu) {
    const bp = buy[d]?.pnl ?? 0, sp = sell[d]?.pnl ?? 0
    console.log(d, '| BUY:', Math.round(bp), '| SELL:', Math.round(sp), '| SELL better:', sp > bp ? 'YES (+' + Math.round(sp - bp) + ')' : 'NO (' + Math.round(sp - bp) + ')')
}
console.log()
console.log('Gap-up day BUY total:', Math.round(gu.reduce((s,d) => s + (buy[d]?.pnl ?? 0), 0)))
console.log('Gap-up day SELL total:', Math.round(gu.reduce((s,d) => s + (sell[d]?.pnl ?? 0), 0)))
