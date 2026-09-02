#!/usr/bin/env node
/**
 * Generates PNG icons for PWA from the existing favicon.svg.
 * Run once: node scripts/generate-pwa-icons.js
 * Requires: npm install --save-dev sharp
 */

const sharp = require('sharp');
const path = require('path');
const fs = require('fs');

const svgPath = path.join(__dirname, '../public/favicon.svg');
const iconsDir = path.join(__dirname, '../public/icons');
const publicDir = path.join(__dirname, '../public');

const sizes = [
  { size: 512, name: 'icon-512.png' },
  { size: 192, name: 'icon-192.png' },
  { size: 180, name: 'apple-touch-icon.png' },
];

async function generate() {
  if (!fs.existsSync(svgPath)) {
    console.error('favicon.svg not found at', svgPath);
    process.exit(1);
  }

  fs.mkdirSync(iconsDir, { recursive: true });

  const svgBuffer = fs.readFileSync(svgPath);

  for (const { size, name } of sizes) {
    const outPath = path.join(iconsDir, name);
    await sharp(svgBuffer, { density: Math.ceil((size / 32) * 72) })
      .resize(size, size)
      .png()
      .toFile(outPath);
    console.log(`✓ icons/${name} (${size}×${size})`);
  }

  // apple-touch-icon also needs to live at the public root (iOS auto-discovery)
  fs.copyFileSync(
    path.join(iconsDir, 'apple-touch-icon.png'),
    path.join(publicDir, 'apple-touch-icon.png')
  );
  console.log('✓ apple-touch-icon.png (copied to public/)');
  console.log('\nDone. Commit public/icons/ and public/apple-touch-icon.png.');
}

generate().catch((err) => {
  console.error(err);
  process.exit(1);
});
