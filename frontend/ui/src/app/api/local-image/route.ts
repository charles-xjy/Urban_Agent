import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

export const runtime = "nodejs";

// 卫星影像目录：相对于 Next.js 项目根（ui/）上两层到 Urban_Agent/data/satellite_images
const IMAGE_DIR = path.resolve(
  process.cwd(),
  "..",
  "..",
  "data",
  "satellite_images",
);

const ALLOWED_EXT = new Set([".jpg", ".jpeg", ".png", ".tif", ".tiff"]);

export async function GET(req: NextRequest) {
  const file = req.nextUrl.searchParams.get("file") ?? "";

  // 安全校验：只允许纯文件名，禁止路径穿越
  if (
    !file ||
    file.includes("..") ||
    file.includes("/") ||
    file.includes("\\")
  ) {
    return NextResponse.json({ error: "Invalid filename" }, { status: 400 });
  }
  if (!ALLOWED_EXT.has(path.extname(file).toLowerCase())) {
    return NextResponse.json({ error: "Unsupported file type" }, { status: 400 });
  }

  const filePath = path.join(IMAGE_DIR, file);
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const buffer = fs.readFileSync(filePath);
  const ext = path.extname(file).toLowerCase();
  const contentType =
    ext === ".png" ? "image/png" :
    ext === ".tif" || ext === ".tiff" ? "image/tiff" :
    "image/jpeg";

  return new NextResponse(buffer, {
    headers: { "Content-Type": contentType },
  });
}
