import Daily, { type DailyCallOptions } from "@daily-co/daily-js";

/**
 * The SmallWebRTC transport manages mic/devices through a Daily call object, which by default
 * downloads its call-machine bundle from Daily's CDN when the mic starts. We serve those bundles
 * from /public/daily (matching the installed daily-js version) so a blocked or flaky CDN can't
 * break calls. The transport reuses an existing call instance, so we create it first, and since
 * its startCamera() passes its own dailyConfig (which would drop ours) we merge ours back in.
 */
export function ensureLocalDailyCallObject() {
  if (typeof window === "undefined" || Daily.getCallInstance()) return;
  const localConfig = { bundlePathOverride: `${window.location.origin}/daily` };
  const call = Daily.createCallObject({ dailyConfig: localConfig });
  const startCamera = call.startCamera.bind(call);
  call.startCamera = (props: DailyCallOptions = {}) =>
    startCamera({ ...props, dailyConfig: { ...props.dailyConfig, ...localConfig } });
}
