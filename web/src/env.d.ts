import type { AMapNamespace } from "./amap";
import type { TeslaSlipUI } from "./prefs";

export {};

declare global {
  interface Window {
    AMap?: AMapNamespace;
    teslaslipUI: TeslaSlipUI;
    _AMapSecurityConfig?: { securityJsCode: string };
  }
}
