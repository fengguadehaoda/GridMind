/**
 * 设备实时监控 API（V1.8.0 final-audit P1：统一走共享 httpClient——
 * 401 自动 refresh 重放 + Bearer 自动注入；生产 access TTL 过期后监控不再中断）。
 */
import httpClient from './httpClient'
import type {
  DevicesResponse,
  DeviceDetailResponse,
  TelemetryResponse,
  HealthScoresResponse,
  HealthCriticalResponse,
} from '../types'

/** GET /devices — 设备总览列表 */
export async function getDevices(): Promise<DevicesResponse> {
  const { data } = await httpClient.get<DevicesResponse>('/devices')
  return data
}

/** GET /devices/{id} — 设备详情（含健康评分、异常、遥测、巡检） */
export async function getDeviceDetail(deviceId: string): Promise<DeviceDetailResponse> {
  const { data } = await httpClient.get<DeviceDetailResponse>(`/devices/${encodeURIComponent(deviceId)}`)
  return data
}

/** GET /devices/{id}/telemetry?hours=... — 设备遥测历史（按时间 DESC） */
export async function getDeviceTelemetry(deviceId: string, hours = 24): Promise<TelemetryResponse> {
  const { data } = await httpClient.get<TelemetryResponse>(
    `/devices/${encodeURIComponent(deviceId)}/telemetry`,
    { params: { hours } },
  )
  return data
}

/** GET /health/scores — 全设备健康评分 */
export async function getHealthScores(): Promise<HealthScoresResponse> {
  const { data } = await httpClient.get<HealthScoresResponse>('/health/scores')
  return data
}

/** GET /health/critical — 严重等级设备列表 */
export async function getHealthCritical(): Promise<HealthCriticalResponse> {
  const { data } = await httpClient.get<HealthCriticalResponse>('/health/critical')
  return data
}
