export type NotificationType = "info" | "warning" | "error";

export interface AppNotification {
    text: string;
    type: NotificationType;
}

export interface PopupNotificationsState {
    messages: AppNotification[];
}