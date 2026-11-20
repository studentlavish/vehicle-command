import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/**
 * Lightweight English / Hindi strings for the screens guards use most:
 * navigation, top bar, Live Monitoring, Plate Review (and gate view), Test drives,
 * and the command palette. Admin-only screens stay in English for now.
 *
 * t("key") returns the string in the chosen language, falling back to English.
 * t("key", { n: 3 }) replaces {n} placeholders.
 */
const STRINGS = {
  en: {
    "nav.group.overview": "Overview",
    "nav.group.records": "Records",
    "nav.group.insights": "Insights",
    "nav.group.admin": "Administration",
    "nav.dashboard": "Dashboard",
    "nav.live": "Live Monitoring",
    "nav.reviews": "Plate Review",
    "nav.search": "Search Vehicle",
    "nav.vehicles": "Vehicle Records",
    "nav.entries": "Entry History",
    "nav.exits": "Exit History",
    "nav.analytics": "Analytics",
    "nav.reports": "Reports",
    "nav.cameras": "Cameras",
    "nav.users": "Users",
    "nav.settings": "Settings",
    "nav.retention.title": "Data retention active",
    "nav.retention.body": "Visit records are kept for 180 days, then removed automatically.",

    "top.search": "Search plate, owner, phone or customer ID…",
    "top.notifications": "Notifications",
    "top.activity": "Live activity",
    "top.live": "Live",
    "top.offline": "Offline",
    "top.caughtUp": "You're all caught up",
    "top.caughtUpBody": "New vehicle entries will appear here in real time.",
    "top.settings": "Settings",
    "top.changePassword": "Change password",
    "top.signOut": "Sign out",
    "top.theme": "Theme",
    "top.theme.light": "Light",
    "top.theme.dark": "Dark",
    "top.theme.system": "System",
    "top.language": "Language",
    "top.openMenu": "Open menu",
    "top.commandHint": "Search or jump to…",

    "cmd.placeholder": "Type a plate, owner or page…",
    "cmd.empty": "Nothing found.",
    "cmd.vehicles": "Vehicles",
    "cmd.pages": "Pages",
    "cmd.actions": "Actions",
    "cmd.searchAll": "Search all records for “{q}”",
    "cmd.toggleTheme": "Switch light / dark",
    "cmd.toggleLanguage": "हिन्दी में बदलें",
    "cmd.gateView": "Open gate view (Plate Review)",

    "live.title": "Live monitoring",
    "live.desc": "Camera feeds and automatic number plate recognition.",
    "live.receiving": "Receiving live events",
    "live.connecting": "Connecting to live events…",
    "live.entered": "entered",
    "live.exited": "exited",
    "live.newVisitor": "New visitor",
    "live.unknownOwner": "Unknown owner",

    "rv.title": "Plate review",
    "rv.desc": "Reads the system wasn't sure about. Check each one against the photo, correct it if needed, then confirm.",
    "rv.pending": "Needs review",
    "rv.resolved": "Resolved",
    "rv.gate": "Gate view",
    "rv.list": "List view",
    "rv.noPlate": "No readable plate",
    "rv.confidence": "{c} confidence",
    "rv.reads": "{n} reads",
    "rv.cameraRead": "camera read",
    "rv.labelNoPlate": "Type the plate if you can see one (or the trade / temporary number)",
    "rv.labelPlate": "Number plate — check against the photo",
    "rv.guesses": "Reader's best guesses — press 1, 2 or 3",
    "rv.inside": "inside",
    "rv.nonstandard": "Non-standard plate (trade, temporary or foreign) — save exactly as typed",
    "rv.confirm": "Confirm",
    "rv.notPlate": "Not a plate",
    "rv.notVehicle": "Not a vehicle",
    "rv.shortcuts": "Shortcuts: Enter confirm · R reject · 1–3 pick a guess · J/K next/previous",
    "rv.allCaught": "All caught up",
    "rv.allCaughtBody": "Every camera read has been confirmed. New uncertain reads will appear here automatically.",
    "rv.left": "{n} left",
    "rv.confirmed": "{p} — {e} recorded",
    "rv.entry": "entry",
    "rv.exit": "exit",
    "rv.already": "{p} confirmed",
    "rv.alreadyBody": "Already recorded — no new entry created.",
    "rv.discarded": "Read discarded",
    "rv.cantConfirm": "Couldn't confirm",
    "rv.cantDiscard": "Couldn't discard",

    "td.title": "Test drives",
    "td.desc": "Showroom cars tagged “Test drive” in Known plates",
    "td.out": "Out now",
    "td.in": "At the showroom",
    "td.none": "No test-drive cars yet",
    "td.noneBody": "Tag demo cars as “Test drive” under Cameras → Known plates to track them here.",
    "td.allIn": "All test-drive cars are at the showroom",
    "td.outFor": "out {m} min",
    "td.overdue": "Over {m} min",
    "td.driver": "Driver (staff)",
    "td.customer": "Customer",
    "td.save": "Save",
    "td.saved": "Saved",
    "td.who": "Who's driving?",
    "td.today": "Back today",
    "td.min": "{m} min",
  },
  hi: {
    "nav.group.overview": "सारांश",
    "nav.group.records": "रिकॉर्ड",
    "nav.group.insights": "विश्लेषण",
    "nav.group.admin": "प्रबंधन",
    "nav.dashboard": "डैशबोर्ड",
    "nav.live": "लाइव निगरानी",
    "nav.reviews": "प्लेट जाँच",
    "nav.search": "गाड़ी खोजें",
    "nav.vehicles": "गाड़ी रिकॉर्ड",
    "nav.entries": "प्रवेश इतिहास",
    "nav.exits": "निकास इतिहास",
    "nav.analytics": "विश्लेषण",
    "nav.reports": "रिपोर्ट",
    "nav.cameras": "कैमरे",
    "nav.users": "उपयोगकर्ता",
    "nav.settings": "सेटिंग्स",
    "nav.retention.title": "डेटा अवधि नियम चालू",
    "nav.retention.body": "विज़िट रिकॉर्ड 180 दिन रखे जाते हैं, फिर अपने-आप हट जाते हैं।",

    "top.search": "नंबर प्लेट, मालिक, फ़ोन या ग्राहक ID खोजें…",
    "top.notifications": "सूचनाएँ",
    "top.activity": "लाइव गतिविधि",
    "top.live": "लाइव",
    "top.offline": "ऑफ़लाइन",
    "top.caughtUp": "कोई नई सूचना नहीं",
    "top.caughtUpBody": "नई गाड़ियों का प्रवेश यहाँ तुरंत दिखेगा।",
    "top.settings": "सेटिंग्स",
    "top.changePassword": "पासवर्ड बदलें",
    "top.signOut": "लॉग आउट",
    "top.theme": "थीम",
    "top.theme.light": "लाइट",
    "top.theme.dark": "डार्क",
    "top.theme.system": "सिस्टम",
    "top.language": "भाषा",
    "top.openMenu": "मेनू खोलें",
    "top.commandHint": "खोजें या पेज पर जाएँ…",

    "cmd.placeholder": "प्लेट नंबर, मालिक या पेज लिखें…",
    "cmd.empty": "कुछ नहीं मिला।",
    "cmd.vehicles": "गाड़ियाँ",
    "cmd.pages": "पेज",
    "cmd.actions": "क्रियाएँ",
    "cmd.searchAll": "सभी रिकॉर्ड में “{q}” खोजें",
    "cmd.toggleTheme": "लाइट / डार्क बदलें",
    "cmd.toggleLanguage": "Switch to English",
    "cmd.gateView": "गेट व्यू खोलें (प्लेट जाँच)",

    "live.title": "लाइव निगरानी",
    "live.desc": "कैमरा फ़ीड और अपने-आप नंबर प्लेट पहचान।",
    "live.receiving": "लाइव अपडेट मिल रहे हैं",
    "live.connecting": "लाइव अपडेट से जुड़ रहे हैं…",
    "live.entered": "अंदर आई",
    "live.exited": "बाहर गई",
    "live.newVisitor": "नया आगंतुक",
    "live.unknownOwner": "मालिक अज्ञात",

    "rv.title": "प्लेट जाँच",
    "rv.desc": "जिन प्लेटों पर सिस्टम को भरोसा नहीं था। हर एक को फ़ोटो से मिलाएँ, ज़रूरत हो तो सुधारें, फिर पक्का करें।",
    "rv.pending": "जाँच बाकी",
    "rv.resolved": "पूरी हुई",
    "rv.gate": "गेट व्यू",
    "rv.list": "सूची व्यू",
    "rv.noPlate": "प्लेट पढ़ी नहीं जा सकी",
    "rv.confidence": "भरोसा: {c}",
    "rv.reads": "{n} बार पढ़ी गई",
    "rv.cameraRead": "कैमरे ने पढ़ा",
    "rv.labelNoPlate": "अगर प्लेट दिख रही है तो लिखें (या ट्रेड / अस्थायी नंबर)",
    "rv.labelPlate": "नंबर प्लेट — फ़ोटो से मिलाएँ",
    "rv.guesses": "सिस्टम के सबसे अच्छे अनुमान — 1, 2 या 3 दबाएँ",
    "rv.inside": "अंदर है",
    "rv.nonstandard": "अलग तरह की प्लेट (ट्रेड, अस्थायी या विदेशी) — जैसा लिखा वैसा ही सेव करें",
    "rv.confirm": "पक्का करें",
    "rv.notPlate": "प्लेट नहीं है",
    "rv.notVehicle": "गाड़ी नहीं है",
    "rv.shortcuts": "शॉर्टकट: Enter पक्का करें · R रद्द करें · 1–3 अनुमान चुनें · J/K अगला/पिछला",
    "rv.allCaught": "सब जाँच हो गई",
    "rv.allCaughtBody": "हर कैमरा रीड पक्की हो चुकी है। नई अनिश्चित रीड यहाँ अपने-आप आएँगी।",
    "rv.left": "{n} बाकी",
    "rv.confirmed": "{p} — {e} दर्ज",
    "rv.entry": "प्रवेश",
    "rv.exit": "निकास",
    "rv.already": "{p} पक्का किया",
    "rv.alreadyBody": "पहले से दर्ज है — नया प्रवेश नहीं बना।",
    "rv.discarded": "रीड हटा दी गई",
    "rv.cantConfirm": "पक्का नहीं हो सका",
    "rv.cantDiscard": "हटाया नहीं जा सका",

    "td.title": "टेस्ट ड्राइव",
    "td.desc": "Known plates में “Test drive” टैग वाली शोरूम गाड़ियाँ",
    "td.out": "अभी बाहर",
    "td.in": "शोरूम में",
    "td.none": "अभी कोई टेस्ट-ड्राइव गाड़ी नहीं",
    "td.noneBody": "डेमो गाड़ियों को Cameras → Known plates में “Test drive” टैग करें।",
    "td.allIn": "सभी टेस्ट-ड्राइव गाड़ियाँ शोरूम में हैं",
    "td.outFor": "{m} मिनट से बाहर",
    "td.overdue": "{m} मिनट से ज़्यादा",
    "td.driver": "ड्राइवर (स्टाफ़)",
    "td.customer": "ग्राहक",
    "td.save": "सेव करें",
    "td.saved": "सेव हो गया",
    "td.who": "कौन चला रहा है?",
    "td.today": "आज वापस आईं",
    "td.min": "{m} मिनट",
  },
};

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "hi", label: "हिन्दी" },
];

const KEY = "vc-lang";
const I18nContext = createContext({ lang: "en", setLang: () => {}, t: (k) => k });

function readLang() {
  try {
    const v = window.localStorage.getItem(KEY);
    return v && STRINGS[v] ? v : "en";
  } catch {
    return "en";
  }
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(readLang);
  const setLang = useCallback((l) => {
    if (!STRINGS[l]) return;
    setLangState(l);
    try { window.localStorage.setItem(KEY, l); } catch { /* per-device preference only */ }
  }, []);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const t = useCallback((key, vars) => {
    let s = STRINGS[lang]?.[key] ?? STRINGS.en[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
    return s;
  }, [lang]);
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useT() {
  return useContext(I18nContext);
}
