// turndown (MIT)은 형식 파일을 싣지 않는다. 이 앱이 쓰는 부분만 적는다 (lib/capture.ts).
declare module "turndown" {
  interface Options {
    headingStyle?: "setext" | "atx";
    codeBlockStyle?: "indented" | "fenced";
    bulletListMarker?: "-" | "+" | "*";
  }
  interface Rule {
    filter: string | string[] | ((node: Node) => boolean);
    replacement: (content: string, node: Node) => string;
  }
  export default class TurndownService {
    constructor(options?: Options);
    turndown(input: string | Node): string;
    remove(filter: string | string[]): this;
    addRule(key: string, rule: Rule): this;
  }
}
