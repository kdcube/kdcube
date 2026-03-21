export type NotificationType = "info" | "warning" | "error";

export interface AppNotification {
    id: string;
    text: string;
    type: NotificationType;
}

export type NewNotification = Omit<AppNotification, "id">;  

export interface PopupNotificationsState {
    messages: AppNotification[];
}