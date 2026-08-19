#!/usr/bin/env node
/*
 * Build the readable Ozon keyword growth weekly report (.xlsx) from the ranked
 * CSV. Pure Node standard library: the .xlsx is written directly as a zip of
 * XML worksheets, no external package needed.
 *
 * Usage:
 *   node build_readable_weekly_report.mjs <rank.csv> <out.xlsx> [preview-dir]
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { deflateRawSync } from "node:zlib";

const [, , inputPath, outputPath, previewDir] = process.argv;
if (!inputPath || !outputPath) {
  console.error("usage: node build_readable_weekly_report.mjs <rank.csv> <out.xlsx> [preview-dir]");
  process.exit(2);
}

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------
function parseCsv(text) {
  const clean = text.replace(/^\ufeff/, "");
  const lines = clean.replace(/\r\n/g, "\n").split("\n").filter((line) => line.trim() !== "");
  if (!lines.length) return { header: [], rows: [] };
  const parse = (line) => {
    const cells = [];
    let current = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (inQuotes) {
        if (ch === '"') {
          if (line[i + 1] === '"') { current += '"'; i += 1; }
          else inQuotes = false;
        } else current += ch;
      } else if (ch === '"') inQuotes = true;
      else if (ch === ",") { cells.push(current); current = ""; }
      else current += ch;
    }
    cells.push(current);
    return cells;
  };
  const header = parse(lines[0]);
  const rows = lines.slice(1).map(parse);
  return { header, rows };
}

function rowObjects(header, rows) {
  return rows.map((cells) => {
    const obj = {};
    header.forEach((key, index) => { obj[key] = cells[index] ?? ""; });
    return obj;
  });
}

function num(value) {
  const n = Number(String(value ?? "").replace(/[%,\s]/g, ""));
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------------------
// Minimal xlsx writer (zip of XML, inline strings)
// ---------------------------------------------------------------------------
const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let crc = -1;
  for (let i = 0; i < buffer.length; i += 1) {
    crc = CRC_TABLE[(crc ^ buffer[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ -1) >>> 0;
}

function xmlEscape(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function columnName(index) {
  let name = "";
  let n = index;
  while (n >= 0) {
    name = String.fromCharCode(65 + (n % 26)) + name;
    n = Math.floor(n / 26) - 1;
  }
  return name;
}

function sheetXml(rows) {
  const rowXml = rows.map((cells, rowIndex) => {
    const cellsXml = cells.map((cell, colIndex) => {
      const ref = `${columnName(colIndex)}${rowIndex + 1}`;
      if (typeof cell === "number" && Number.isFinite(cell)) {
        return `<c r="${ref}"><v>${cell}</v></c>`;
      }
      return `<c r="${ref}" t="inlineStr"><is><t>${xmlEscape(cell)}</t></is></c>`;
    }).join("");
    return `<row r="${rowIndex + 1}">${cellsXml}</row>`;
  }).join("");
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rowXml}</sheetData></worksheet>`;
}

function buildXlsx(sheets) {
  const sheetNames = sheets.map((sheet) => sheet.name);
  const contentTypes = [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
    '<Default Extension="xml" ContentType="application/xml"/>',
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ...sheetNames.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`),
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    "</Types>",
  ].join("");

  const rels = [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>',
    "</Relationships>",
  ].join("");

  const workbook = [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
    "<sheets>",
    ...sheetNames.map((name, i) => `<sheet name="${xmlEscape(name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`),
    "</sheets>",
    "</workbook>",
  ].join("");

  const workbookRels = [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ...sheetNames.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`),
    `<Relationship Id="rId${sheetNames.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>`,
    "</Relationships>",
  ].join("");

  const styles = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf/></cellXfs></styleSheet>';

  const files = [
    { name: "[Content_Types].xml", content: contentTypes },
    { name: "_rels/.rels", content: rels },
    { name: "xl/workbook.xml", content: workbook },
    { name: "xl/_rels/workbook.xml.rels", content: workbookRels },
    { name: "xl/styles.xml", content: styles },
    ...sheetNames.map((_, i) => ({ name: `xl/worksheets/sheet${i + 1}.xml`, content: sheetXml(sheets[i].rows) })),
  ];

  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const file of files) {
    const nameBuffer = Buffer.from(file.name, "utf-8");
    const contentBuffer = Buffer.from(file.content, "utf-8");
    const compressed = deflateRawSync(contentBuffer);
    const crc = crc32(contentBuffer);
    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(20, 4);
    localHeader.writeUInt16LE(0x0800, 6);
    localHeader.writeUInt16LE(8, 8);
    localHeader.writeUInt32LE(crc, 14);
    localHeader.writeUInt32LE(compressed.length, 18);
    localHeader.writeUInt32LE(contentBuffer.length, 22);
    localHeader.writeUInt16LE(nameBuffer.length, 26);
    localHeader.writeUInt16LE(0, 28);
    localParts.push(localHeader, nameBuffer, compressed);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(20, 4);
    centralHeader.writeUInt16LE(20, 6);
    centralHeader.writeUInt16LE(0x0800, 8);
    centralHeader.writeUInt16LE(8, 10);
    centralHeader.writeUInt32LE(crc, 16);
    centralHeader.writeUInt32LE(compressed.length, 20);
    centralHeader.writeUInt32LE(contentBuffer.length, 24);
    centralHeader.writeUInt16LE(nameBuffer.length, 28);
    centralHeader.writeUInt32LE(offset, 42);
    centralParts.push(centralHeader, nameBuffer);
    offset += localHeader.length + nameBuffer.length + compressed.length;
  }
  const centralBuffer = Buffer.concat(centralParts);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(0, 4);
  eocd.writeUInt16LE(0, 6);
  eocd.writeUInt16LE(files.length, 8);
  eocd.writeUInt16LE(files.length, 10);
  eocd.writeUInt32LE(centralBuffer.length, 12);
  eocd.writeUInt32LE(offset, 16);
  eocd.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, centralBuffer, eocd]);
}

// ---------------------------------------------------------------------------
// Report shaping
// ---------------------------------------------------------------------------
const inputText = readFileSync(inputPath, "utf-8");
const { header, rows } = parseCsv(inputText);
const data = rowObjects(header, rows);

const label = (row, key, fallback = "") => String(row[key] ?? fallback);
const EXCLUDED = data.filter((row) => label(row, "bucket") === "exclude");
const ACTIVE = data.filter((row) => label(row, "bucket") !== "exclude");
const GROWTH = [...ACTIVE].sort((a, b) => num(a.growth_rank) - num(b.growth_rank));
const OPPORTUNITY = [...ACTIVE].sort((a, b) => num(b.opportunity_score) - num(a.opportunity_score));

const GROWTH_HEADER = ["增长排名", "关键词", "月搜热度", "月搜增长%", "竞品数", "竞对数", "广告竞品数", "置信度"];
const OPPORTUNITY_HEADER = ["机会排名", "关键词", "需求动量", "竞争", "集中度", "稳定性", "运营风险", "总分"];
const EXCLUDE_HEADER = ["关键词", "排除原因", "假增长风险"];

function growthRow(row, index) {
  return [index + 1, label(row, "keyword"),
    num(row.monthly_search_heat) ?? label(row, "monthly_search_heat"),
    num(row.monthly_growth_percent) ?? label(row, "monthly_growth_percent"),
    num(row.competitor_count) ?? label(row, "competitor_count"),
    num(row.competitor_seller_count) ?? label(row, "competitor_seller_count"),
    num(row.ad_competitor_count) ?? label(row, "ad_competitor_count"),
    label(row, "confidence")];
}

function opportunityRow(row, index) {
  return [index + 1, label(row, "keyword"),
    num(row.demand_momentum) ?? 0, num(row.competition) ?? 0, num(row.concentration) ?? 0,
    num(row.stability) ?? 0, num(row.operational_risk) ?? 0, num(row.opportunity_score) ?? 0];
}

function excludeRow(row) {
  return [label(row, "keyword"), label(row, "exclude_reason"), label(row, "false_growth_risk")];
}

const topKeywords = GROWTH.slice(0, 5);

const dashboard = [
  ["Ozon 关键词增长机会周报"],
  [],
  ["一、结论（优先关键词方向）"],
  ...topKeywords.map((row) => [`${label(row, "keyword")}：月搜增长 ${label(row, "monthly_growth_percent")}%（置信 ${label(row, "confidence")}）`]),
  [],
  ["二、增长榜 TOP10"],
  GROWTH_HEADER,
  ...GROWTH.slice(0, 10).map((row, i) => growthRow(row, i)),
  [],
  ["三、机会榜 TOP10"],
  OPPORTUNITY_HEADER,
  ...OPPORTUNITY.slice(0, 10).map((row, i) => opportunityRow(row, i)),
];

const sheets = [
  { name: "决策看板", rows: dashboard },
  { name: "关键词增长榜", rows: [GROWTH_HEADER, ...GROWTH.map((row, i) => growthRow(row, i))] },
  { name: "关键词机会榜", rows: [OPPORTUNITY_HEADER, ...OPPORTUNITY.map((row, i) => opportunityRow(row, i))] },
  { name: "排除项", rows: [EXCLUDE_HEADER, ...EXCLUDED.map(excludeRow)] },
  { name: "完整数据", rows: [header, ...data.map((row) => header.map((key) => { const n = num(row[key]); return n !== null ? n : String(row[key] ?? ""); }))] },
];

const buffer = buildXlsx(sheets);
writeFileSync(outputPath, buffer);

if (previewDir) {
  mkdirSync(previewDir, { recursive: true });
  const preview = [
    "# Ozon 关键词增长机会周报（预览）",
    "",
    "## 结论（优先关键词方向）",
    ...topKeywords.map((row) => `- ${label(row, "keyword")}：月搜增长 ${label(row, "monthly_growth_percent")}%（置信 ${label(row, "confidence")}）`),
    "",
    `## 排除项（${EXCLUDED.length}）`,
    ...EXCLUDED.map((row) => `- ${label(row, "keyword")}：${label(row, "exclude_reason")}`),
    "",
    "完整数据见 Excel 工作簿。",
  ].join("\n");
  writeFileSync(path.join(previewDir, "report.md"), preview);
}

console.log(`wrote ${outputPath} (${sheets.length} sheets)`);
