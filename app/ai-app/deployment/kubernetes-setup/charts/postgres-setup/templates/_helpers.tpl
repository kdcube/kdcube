{{- define "postgres-setup.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "postgres-setup.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "postgres-setup.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "postgres-setup.imageTag" -}}
{{- $root := .root -}}
{{- $explicitTag := .explicitTag | default "" -}}
{{- if $explicitTag -}}
{{- $explicitTag -}}
{{- else if $root.Values.platform.ref -}}
{{- $root.Values.platform.ref -}}
{{- else if and $root.Values.platform $root.Values.platform.config $root.Values.platform.config.version -}}
{{- $root.Values.platform.config.version -}}
{{- else if and $root.Values.config $root.Values.config.version -}}
{{- $root.Values.config.version -}}
{{- else -}}
{{- $root.Chart.AppVersion -}}
{{- end -}}
{{- end -}}
