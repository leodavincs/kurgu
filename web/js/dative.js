// dative.js — the Turkish "-e hali" of a (usually foreign) name with an apostrophe: Newton'a, Codex'e. Pure function, no DOM.
// Vowel harmony follows how a name is PRONOUNCED, not how it is spelled: "Claude" is said "Klod" (back vowel) and takes 'a, although its last written
// vowel is e. Names that the spelling rule gets wrong are listed here; the rule is the fallback.
const SUFFIX = { claude: 'a', codex: 'e', newton: 'a', gemini: 'ye', copilot: 'a', cursor: 'a', aider: 'a', chatgpt: 'ye', gpt: 'ye', grok: 'a', llama: "ya", mistral: 'a', kurgu: 'ya' };

export function dative(name) {
  const known = SUFFIX[String(name).toLowerCase()];
  if (known) return name + "'" + known;
  const vowels = name.toLowerCase().match(/[aeıioöuü]/g);
  if (!vowels) return name + "'e";
  const v = vowels[vowels.length - 1], back = 'aıou'.includes(v), endsVowel = /[aeıioöuü]$/i.test(name);
  return name + "'" + (endsVowel ? 'y' : '') + (back ? 'a' : 'e');
}
