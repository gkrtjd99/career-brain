// tesseract.js의 ESM 묶음 (dist). 형식은 패키지의 src/index.d.ts를 쓴다.
declare module "tesseract.js/dist/tesseract.esm.min.js" {
  import type Tesseract from "tesseract.js";
  const mod: typeof Tesseract;
  export default mod;
}
