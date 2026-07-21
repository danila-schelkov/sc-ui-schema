export type UI = Source & DefaultAnimation & {
    id: string;
    priority: number;

    type: string;
    subtype: string;
    center_to_screen: boolean;
    disable_audio: boolean;
    dont_animate_skins_on_lowend_devices: boolean; // Since BSv68

    container_prefix: string;
    container_bg: string;
    container_center: string;
    container_top: string;
    container_bottom: string;
    container_bottom_right: string;
    container_left: string;
    container_right: string;
    container_top_left: string;
    container_top_right: string;
    container_bottom_left: string;

    copy_configs: string | string[];

    hide: ChildReference[];
    show: ChildReference[];

    hide_binding: BindingId[];
    show_binding: BindingId[];

    interactive: ChildReference[];
    uninteractive: ChildReference[];

    replace: Replace[];
    bindings: { [key: string]: ChildReference };
    buttons: { [key: string]: ChildReference };
    move: Move[];

    skins: { [key: string]: Skin };

    animation: DefaultAnimation & { [key: string]: Animation };
    animations: DefaultAnimation & { [key: string]: Animation };
    custom: CustomFields;
};

export type BindingId = string;
export type BindingRef = {
    binding: BindingId;
};

export type Source = {
    sc_file_source:
    | "Undefined"
    | "EventData"
    | "ShopOffer"
    | "AssetIdList"
    | "ChronosFolder"
    | "ClientFile"
    | "OtherTomlConfig";
    sc_file: string;
    sc_file_asset_id_list: string;
    sc_file_chronos: string;
    sc_file_client: string;
    png_file_suffix: string;
    other_toml_config: string;
    export_name: string;
};

export type ChildReferenceObject = {
    find: string;
    parent_binding: BindingId;
    parent_path: string;
    parent_clip: string;
    path: string;
    child_index: number;
};

export type ChildReference = BindingId | ChildReferenceObject;

export type Replace = BindingRef & ChildReferenceObject & Source;

export type AnimationType =
    | "IDLE"
    | "HAPPY"
    | "HAPPY_LOOP"
    | "SAD"
    | "SAD_LOOP"
    | "LOBBY"
    | "LOBBY_LOOP"
    | "Surprise_ANIM"
    | "HERO_SCREEN"
    | "HERO_SCREEN_LOOP"
    | "HERO_SCREEN_IDLE"
    | "SIGNATURE"
    | "HAPPY_ENTER"
    | "SAD_ENTER"
    | "PROFILE"
    | "INTRO"
    | "ENV_LOOP"
    | "SHOP_GROUP_PROFILE"
    | "BRAWLPASS_POPUP_PROFILE"
    | "GACHA_OVERRIDE"
    | "WALK"
    | "SECONDARY"
    | "RECRUITROAD_OVERRIDE";

export type Vec2 = [number, number];
export type Vec3 = [number, number, number];

export type Skin = BindingRef & ChildReferenceObject & {
    skin: string;
    hero: string;
    selected_hero: boolean;
    load_immediately: boolean;
    use_portrait_camera: boolean;
    portrait: boolean;
    dont_animate: boolean;
    dont_animate_on_lowend_devices: boolean; // Since BSv68
    camera_file: string;
    position: Vec3;
    offset: Vec2;
    scale: number;
    rotation: number;
    roll: number;
    animation: AnimationType;
    animation_next: AnimationType;
};

export type Animation = BindingRef & ChildReferenceObject & {
    start: string;
    end: string;
    frame: number;
    start_frame: number;
    end_frame: number;
    loop: boolean;  // Since BSv68
    apply_to_children: boolean;
    next_animation: string;
    animation_next: string;
    next: string;
    also_play: string | string[];
};

export type DefaultAnimation = { default_animation: string | { self: string } | Animation };

export type Color = number | string;

export type SetText = BindingRef & ChildReferenceObject & {
    tid: string;
    text: string;
    auto_scale: boolean; // Since BSv68
    font_size: number; // Since BSv68
    color: Color;
    outline: Color;
    gradient_name: string;
    gradient: { colors: Color[]; speed: number; scale: number };
};

export type Move = BindingRef & ChildReferenceObject & {
    offset: Vec2;
    scale: Vec2 | number;
    rotation: number;
};

export type CustomFields = {
    int: { [key: string]: number };
    bool: { [key: string]: boolean };
    float: { [key: string]: number };
    vector2: { [key: string]: Vec2 };
    vector3: { [key: string]: Vec3 };
    string: { [key: string]: string }
}
export type CustomType = keyof CustomFields;
