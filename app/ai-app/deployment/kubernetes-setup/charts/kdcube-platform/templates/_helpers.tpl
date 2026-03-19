{{- define "kdcube-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kdcube-platform.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "kdcube-platform.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "kdcube-platform.imageTag" -}}
{{- $root := .root -}}
{{- $serviceTag := .serviceTag | default "" -}}
{{- if $serviceTag -}}
{{- $serviceTag -}}
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
