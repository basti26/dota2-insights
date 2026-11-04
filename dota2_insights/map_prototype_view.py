"""Local Tk comparison surface and deterministic image export for map candidates."""
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from .map_holdout import local
from .map_prototype import ViewTransform, open_scene, project_marker, ward_candidate_xy
from .virtual_map import VirtualMap
from .ward_map import PHASES, visible_points, status as ward_status

COLORS = {'npc_dota_lotus_pool': '#5ee6c9', 'npc_dota_watch_tower': '#d6a5ff',
    'dota_item_rune_spawner_powerup': '#62bbff', 'dota_item_rune_spawner_bounty': '#f8ca69',
    'npc_dota_fort': '#ff8d85', 'npc_dota_barracks': '#a9bbc6',
    'ent_dota_fountain': '#a6edaf', 'npc_dota_xp_fountain': '#f5a5d7'}
NAMES = {'npc_dota_lotus_pool': '莲花池', 'npc_dota_watch_tower': '瞭望塔',
    'dota_item_rune_spawner_powerup': '强化符', 'dota_item_rune_spawner_bounty': '赏金符',
    'npc_dota_fort': '遗迹', 'npc_dota_barracks': '兵营',
    'ent_dota_fountain': '泉水', 'npc_dota_xp_fountain': '经验泉'}


@lru_cache(maxsize=32)
def font(size):
    from PIL import ImageFont
    for filename in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/segoeui.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(filename).is_file():
            return ImageFont.truetype(filename, size)
    return ImageFont.load_default()


def texture_view(texture, footprint, view, *, categorical=False):
    from PIL import Image
    sx = texture.width/(footprint['max_x']-footprint['min_x'])
    sy = texture.height/(footprint['max_y']-footprint['min_y'])
    left, top = view.world(0, 0)
    right_one, down_one = view.world(1, 1)
    a, b = (right_one-left)*sx, (top-down_one)*sy
    c, d = (left-footprint['min_x'])*sx, (footprint['max_y']-top)*sy
    return texture.transform((view.width, view.height), Image.Transform.AFFINE, (a, 0, c, 0, b, d),
        resample=Image.Resampling.NEAREST if categorical else Image.Resampling.BILINEAR, fillcolor='#111e29')


def world_image(scene, texture, view, *, selected=None, physics=False, labels=False, wards=False, selected_ward=None,
        grid_texture=None, grid_layer=False, tree_layer=False, ward_phase='全部'):
    from PIL import Image, ImageDraw
    image = texture_view(texture, scene['collision_texture']['footprint'], view) if physics else Image.new('RGB', (view.width, view.height), '#111e29')
    roots=scene.get('root_assets')
    if grid_layer and grid_texture is not None and roots:
        image=texture_view(grid_texture,roots['grid_info']['xy_bounds_candidate'],view,categorical=True)
    draw = ImageDraw.Draw(image)
    for value in range(-12000, 12001, 2000):
        x, y = view.pixel(value, value)
        draw.line((x, 0, x, view.height), fill='#45616a' if value == 0 else '#283e4b')
        draw.line((0, y, view.width, y), fill='#45616a' if value == 0 else '#283e4b')
        if 0 <= x < view.width-40: draw.text((x+3, view.height-22), str(value), font=font(12), fill='#b6cbd3')
        if 0 <= y < view.height-22: draw.text((3, y+2), str(value), font=font(12), fill='#b6cbd3')
    bounds = scene['bounds']
    a = view.pixel(bounds['min_x'], bounds['max_y'])
    b = view.pixel(bounds['max_x'], bounds['min_y'])
    draw.rectangle((*a, *b), outline='#f0c674', width=2)
    if tree_layer and roots:
        for p in roots['trm']['points']:
            x,y=view.pixel(*p['xy'])
            draw.ellipse((x-1.7,y-1.7,x+1.7,y+1.7),fill='#c5e2cd')
        for p in (roots['tree_comparison'] or {}).get('trm_records_not_paired_to_entities',[]):
            x,y=view.pixel(*p['xy'])
            draw.ellipse((x-6,y-6,x+6,y+6),outline='#ff92e2',width=2)
    if wards and scene.get('ward_candidates'):
        layer = scene['ward_candidates']
        model=view.map_model or VirtualMap.from_dict(scene['virtual_map_model'])
        for p in visible_points(layer,model,ward_phase,allow_unverified=True):
            x,y = view.pixel(*ward_candidate_xy(layer, p,model))
            radius = 8 if p['id'] == selected_ward else 4
            color = '#41f787' if p['ward_type'] == 'observer' else '#ff5b69'
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill=color, outline='#111e29')
            if p['id'] == selected_ward:
                draw.ellipse((x-11,y-11,x+11,y+11), outline='white', width=2)
    for index, marker in enumerate(scene['markers']):
        x, y = view.pixel(*marker['world_xyz'][:2])
        color = COLORS.get(marker['classname'], '#ffffff')
        radius = 8 if marker['id'] == selected else 5
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color, outline='#101a25', width=2)
        if marker['id'] == selected:
            draw.ellipse((x-12, y-12, x+12, y+12), outline='white', width=2)
        if labels or marker['id'] == selected:
            draw.text((x+9, y-16), f"{index+1} {NAMES.get(marker['classname'], marker['classname'])}", font=font(14), fill=color,
                stroke_width=2, stroke_fill='#101a25')
    draw.rectangle((0, 0, view.width, 29), fill='#1c2b37')
    title='GNV原始字节网格：坐标/位含义未认证' if grid_layer else ('碰撞局部坐标 + 实体同原点假设：待验证' if physics else '实体世界坐标 · 黄框为地图声明窗口')
    draw.text((10, 4), title, font=font(15), fill='#f3d393')
    return image


def screenshot_image(scene, package, frame, mode, selected=None, side=620):
    from PIL import Image, ImageDraw
    with Image.open(local(package, frame['image'])) as original:
        image = original.convert('RGB').resize((side, side), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    sx, sy = side/frame['size'][0], side/frame['size'][1]
    for marker in scene['markers']:
        p = project_marker(scene, marker, frame, mode)
        x, y = p[0]*sx, p[1]*sy
        color = '#fff09c' if marker['id'] == selected else '#f393e7'
        radius = 9 if marker['id'] == selected else 4
        draw.line((x-radius, y, x+radius, y), fill=color, width=2)
        draw.line((x, y-radius, x, y+radius), fill=color, width=2)
    # All frozen candidate categories remain visible; watcher candidates are
    # deliberately muted because they contain known terrain false positives.
    for row in scene['comparisons']:
        if row['case'] != frame['case'] or row['frame_index'] != frame['frame_index']:
            continue
        color = '#4ff1f3' if row['icon'] == 'minimap_lotuspool' else '#929c9c'
        for p in row['observed_candidates']:
            x, y = p[0]*sx, p[1]*sy
            draw.ellipse((x-8, y-8, x+8, y+8), outline=color, width=2)
    return image


def comparison_text(scene):
    summaries=scene.get('summaries',{}).get('minimap_lotuspool',{})
    parts=[]
    for key,label in (('asset_window','直接铺满'),('historical_tower_model','旧参考')):
        errors=summaries.get(key,{}).get('errors_reference_360px')
        parts.append(label+(f" {errors['min']:.2f}～{errors['max']:.2f}px" if errors else ' 未提供'))
    return '莲花候选残差（360px基准）：'+' / '.join(parts)+'；集合比较不认证身份或全图。'


def export_overview(package, output):
    """Export the actual renderer at fixed state, without starting a GUI/game."""
    from PIL import Image, ImageDraw
    package = Path(package)
    scene = open_scene(package)
    with Image.open(local(package, scene['collision_texture']['image'])) as source:
        texture = source.convert('RGB')
    if scene.get('root_assets'):
        return export_root_overview(package,output,scene,texture)
    image = Image.new('RGB', (1840, 840), '#101924')
    draw = ImageDraw.Draw(image)
    draw.text((20, 14), '虚拟地图对照原型 / 本地资料 / 构建 '+str(scene['client_build']), font=font(25), fill='#e4edf4')
    draw.text((20, 52), '地标：实体原始世界坐标；碰撞：局部坐标。此图尝试同原点叠加，尚未认证世界放置。', font=font(17), fill='#f3d393')
    frame = scene['frames'][0]
    selected = next(p['id'] for p in scene['markers'] if p['classname'] == 'npc_dota_lotus_pool')
    view = ViewTransform(scale=.028, width=580, height=580, map_model=VirtualMap.from_dict(scene['virtual_map_model']))
    image.paste(world_image(scene, texture, view, selected=selected, physics=True, labels=True, wards=True), (20, 130))
    for x, mode, title in ((620, 'asset_window', '声明窗口铺满截图'), (1220, 'historical_tower_model', '旧塔点矩阵（固定参考）')):
        draw.text((x, 96), title, font=font(20), fill='#e4edf4')
        image.paste(screenshot_image(scene, package, frame, mode, selected, side=580), (x, 130))
    draw.text((20, 737), '青圈：冻结莲花候选；灰圈：其他候选（可能误匹配）；十字：地标预测。三个视图没有重新拟合。', font=font(19), fill='#bed1db')
    draw.text((20, 773), comparison_text(scene), font=font(19), fill='#bed1db')
    if scene.get('ward_candidates'):
        layer = scene['ward_candidates']
        draw.text((20, 808), f"左图另含 {layer['match_id']} 的 {len(layer['points'])} 个历史眼位候选：绿假眼、红真眼；版本/平移未认证，不是右侧场景内的眼。", font=font(16), fill='#f3d393')
    image.save(output)
    return scene


def export_root_overview(package,output,scene,texture):
    from PIL import Image,ImageDraw
    image=Image.new('RGB',(1340,1560),'#101924');draw=ImageDraw.Draw(image)
    draw.text((20,12),'虚拟地图原型 · 四种资料相互对照',font=font(27),fill='#e4edf4')
    roots=scene['root_assets']
    comparison=roots.get('tree_comparison')
    tree_text=f"实体/TRM有{comparison['exact_xy_layer_matches']}条XY与层精确匹配。" if comparison else '未提供实体/TRM对照。'
    draw.text((20,52),tree_text+'碰撞放置、GNV位含义及眼位版本未认证。',font=font(17),fill='#f3d393')
    with Image.open(local(package,roots['grid_image'])) as im:grid_texture=im.convert('RGB')
    view=ViewTransform(scale=.029,width=640,height=640,map_model=VirtualMap.from_dict(scene['virtual_map_model']))
    selected=next(p['id'] for p in scene['markers'] if p['classname']=='npc_dota_lotus_pool')
    panels=[(20,110,'碰撞候选 + 地标 + 历史眼位',world_image(scene,texture,view,physics=True,labels=True,selected=selected,wards=True)),
        (680,110,'旧塔点矩阵 / 冻结截图参考',screenshot_image(scene,package,scene['frames'][0],'historical_tower_model',selected,640)),
        (20,810,'GNV字节类别 + TRM静态坐标目录',world_image(scene,texture,view,grid_texture=grid_texture,grid_layer=True,tree_layer=True,wards=True)),
        (680,810,'声明窗口铺满截图 / 待验假设',screenshot_image(scene,package,scene['frames'][0],'asset_window',selected,640))]
    for x,y,title,panel in panels:
        draw.text((x,y-28),title,font=font(19),fill='#dce8ef');image.paste(panel,(x,y))
    draw.text((20,1475),'GNV颜色仅按原始字节分类，不等于可走/可插眼。浅点=TRM坐标；粉圈=未与实体记录配对的记录。',font=font(17),fill='#bed1db')
    draw.text((20,1505),comparison_text(scene),font=font(17),fill='#bed1db')
    image.save(output)
    return scene


class PrototypeWindow:
    def __init__(self, window, package):
        import tkinter as tk
        from tkinter import ttk
        from PIL import Image
        self.window, self.package = window, Path(package)
        self.scene = open_scene(package)
        with Image.open(local(self.package, self.scene['collision_texture']['image'])) as im:
            self.texture = im.convert('RGB')
        self.grid_texture=None
        self.raw_grid=None
        roots=self.scene.get('root_assets')
        if roots:
            with Image.open(local(self.package,roots['grid_image'])) as im:self.grid_texture=im.convert('RGB')
            from .map_root_assets import grid
            self.raw_grid=grid(local(self.package,roots['grid_bytes']).read_bytes())
            if self.raw_grid['xy_bounds_candidate']!=roots['grid_info']['xy_bounds_candidate']:
                raise ValueError('prototype_grid_texture_frame_mismatch')
        self.view = ViewTransform(map_model=VirtualMap.from_dict(self.scene['virtual_map_model']))
        self.selected = self.scene['markers'][0]['id']
        self.selected_ward = None
        self.drag_start = None
        self.dragged = False
        self.physics = tk.BooleanVar(value=False)
        self.labels = tk.BooleanVar(value=False)
        self.wards = tk.BooleanVar(value=bool(self.scene.get('ward_preview_default',False)))
        self.grid_layer=tk.BooleanVar(value=bool(self.scene.get('ward_preview_default',False) and roots))
        self.tree_layer=tk.BooleanVar(value=False)
        self.ward_phase=tk.StringVar(value='全部')
        self.ward_count=tk.StringVar()
        self.mode = tk.StringVar(value='旧塔点矩阵（固定参考）')
        self.frame_index = tk.StringVar(value=self.frame_names()[0])
        self.marker_name = tk.StringVar(value=self.marker_names()[0])
        self.detail = tk.StringVar()
        self.cursor = tk.StringVar(value='滚轮缩放，按住拖动，单击地标；原始世界坐标不会随视图变化。')
        window.title('Insights · 虚拟地图对照原型（离线）')
        window.geometry('1420x940')
        window.minsize(1040, 720)
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('TFrame', background='#15212d')
        style.configure('TLabel', background='#15212d', foreground='#dfebf1')
        style.configure('TCheckbutton', background='#15212d', foreground='#dfebf1')
        root = ttk.Frame(window, padding=14)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='虚拟地图 · 地标与冻结截图相互对照', font=('Microsoft YaHei UI', 18)).pack(anchor='w')
        ttk.Label(root, text='离线诊断；P2未通过；碰撞世界放置未认证；不改变游戏浮层。', foreground='#f1cd87').pack(anchor='w', pady=(4, 10))
        controls = ttk.Frame(root)
        controls.pack(fill='x')
        ttk.Checkbutton(controls, text='尝试同原点叠加碰撞（未验证）', variable=self.physics, command=self.choose_physics).pack(side='left')
        ttk.Checkbutton(controls, text='地标名称', variable=self.labels, command=self.redraw).pack(side='left', padx=10)
        ttk.Button(controls, text='复位视图', command=self.reset).pack(side='left')
        self.point_select = ttk.Combobox(controls, textvariable=self.marker_name, values=self.marker_names(), state='readonly', width=28)
        self.point_select.pack(side='left', padx=12)
        self.point_select.bind('<<ComboboxSelected>>', self.choose_marker)
        if roots:
            root_controls=ttk.Frame(root);root_controls.pack(fill='x',pady=5)
            ttk.Checkbutton(root_controls,text='GNV原始网格（位含义未核验）',variable=self.grid_layer,command=self.choose_grid).pack(side='left')
            ttk.Checkbutton(root_controls,text=f"TRM静态坐标（{roots['trm']['record_count']}条）",variable=self.tree_layer,command=self.redraw).pack(side='left',padx=10)
            ttk.Label(root_controls,text='浅点=坐标目录；粉圈=未配对记录；不表示当前存活树。',foreground='#f1cd87').pack(side='left')
        ward_controls = ttk.Frame(root)
        ward_controls.pack(fill='x', pady=(5, 0))
        layer = self.scene.get('ward_candidates')
        if layer:
            ttk.Checkbutton(ward_controls, text=f"历史眼位候选：{layer['match_id']} / {len(layer['points'])}个（版本未核验）",
                variable=self.wards, command=self.redraw).pack(side='left')
            ttk.Label(ward_controls, text='绿=假眼；红=真眼；全场分布，不是右侧截图内的眼位。', foreground='#f1cd87').pack(side='left', padx=8)
            phase_controls=ttk.Frame(root);phase_controls.pack(fill='x',pady=(4,0))
            ttk.Label(phase_controls,text='按放置时间：').pack(side='left')
            phase_select=ttk.Combobox(phase_controls,textvariable=self.ward_phase,values=PHASES,state='readonly',width=15)
            phase_select.pack(side='left');phase_select.bind('<<ComboboxSelected>>',self.choose_ward_phase)
            ttk.Label(phase_controls,textvariable=self.ward_count,foreground='#f1cd87').pack(side='left',padx=10)
        frame_controls = ttk.Frame(root)
        frame_controls.pack(fill='x', pady=8)
        frames = ttk.Combobox(frame_controls, textvariable=self.frame_index, values=self.frame_names(), state='readonly', width=34)
        frames.pack(side='left')
        frames.bind('<<ComboboxSelected>>', lambda _: self.redraw())
        modes = ttk.Combobox(frame_controls, textvariable=self.mode, values=['旧塔点矩阵（固定参考）', '声明边界铺满截图（假设）'], state='readonly', width=30)
        modes.pack(side='left', padx=12)
        modes.bind('<<ComboboxSelected>>', lambda _: self.redraw())
        panes = ttk.Frame(root)
        panes.pack(fill='both', expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(0, weight=1)
        self.world_canvas = tk.Canvas(panes, background='#111e29', highlightthickness=0)
        self.shot_canvas = tk.Canvas(panes, background='#111e29', highlightthickness=0)
        self.world_canvas.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        self.shot_canvas.grid(row=0, column=1, sticky='nsew')
        self.world_canvas.bind('<Configure>', lambda _: self.redraw())
        self.shot_canvas.bind('<Configure>', lambda _: self.redraw())
        self.world_canvas.bind('<MouseWheel>', self.wheel)
        self.world_canvas.bind('<ButtonPress-1>', self.press)
        self.world_canvas.bind('<B1-Motion>', self.drag)
        self.world_canvas.bind('<ButtonRelease-1>', self.release)
        self.world_canvas.bind('<Motion>', self.motion)
        ttk.Label(root, textvariable=self.detail, wraplength=1300).pack(anchor='w', pady=(10, 4))
        ttk.Label(root, textvariable=self.cursor).pack(anchor='w')
        ttk.Label(root, text='青圈=冻结莲花候选；灰圈=其他候选（含假阳性）；十字=实体预测。旧截图不是当前运行UI认证。', foreground='#a3bccb').pack(anchor='w', pady=5)
        self.redraw()

    def marker_names(self):
        return [f"{i+1}. {NAMES.get(p['classname'], p['classname'])} ({p['world_xyz'][0]:.0f},{p['world_xyz'][1]:.0f})" for i, p in enumerate(self.scene['markers'])]

    def frame_names(self):
        return [f"{f['case']} · 帧 {f['frame_index']}" for f in self.scene['frames']]

    def choose_marker(self, _=None):
        index = self.marker_names().index(self.marker_name.get())
        self.selected = self.scene['markers'][index]['id']
        self.selected_ward = None
        self.redraw()

    def reset(self):
        self.view = ViewTransform(width=self.view.width, height=self.view.height,
            scale=min(self.view.width, self.view.height)/21500, map_model=self.view.map_model)
        self.redraw()

    def choose_grid(self):
        if self.grid_layer.get():self.physics.set(False)
        self.redraw()

    def choose_physics(self):
        if self.physics.get():self.grid_layer.set(False)
        self.redraw()

    def choose_ward_phase(self,_=None):
        self.selected_ward=None
        if self.selected is None:
            self.selected=self.scene['markers'][0]['id']
            self.marker_name.set(self.marker_names()[0])
        self.redraw()

    def wheel(self, event):
        if event.delta:
            self.view = self.view.zoom(1.2 if event.delta > 0 else 1/1.2, event.x, event.y)
            self.redraw()

    def press(self, event):
        self.drag_start, self.dragged = (event.x, event.y), False

    def drag(self, event):
        if self.drag_start:
            dx, dy = event.x-self.drag_start[0], event.y-self.drag_start[1]
            if abs(dx)+abs(dy) > 2 or self.dragged:
                self.view = self.view.pan(dx, dy)
                self.dragged = True
                self.drag_start = (event.x, event.y)
                self.redraw()

    def release(self, event):
        if not self.dragged:
            candidates = [(sum((a-b)**2 for a,b in zip(self.view.pixel(*p['world_xyz'][:2]), (event.x,event.y))), i, p)
                for i,p in enumerate(self.scene['markers'])]
            distance, index, point = min(candidates, key=lambda p: p[0])
            layer = self.scene.get('ward_candidates')
            points=visible_points(layer,self.view.map_model,self.ward_phase.get(),allow_unverified=True) if layer else []
            if self.wards.get() and points:
                ward_distance, ward = min(((sum((a-b)**2 for a,b in zip(self.view.pixel(*ward_candidate_xy(layer, p,self.view.map_model)),
                    (event.x,event.y))), p) for p in points), key=lambda pair: pair[0])
                if ward_distance <= 14**2 and ward_distance < distance:
                    self.selected_ward = ward['id']
                    self.selected = None
                    self.redraw()
                    self.drag_start = None
                    return
            if distance <= 14**2:
                self.selected = point['id']
                self.selected_ward = None
                self.marker_name.set(self.marker_names()[index])
                self.redraw()
        self.drag_start = None

    def motion(self, event):
        x,y = self.view.world(event.x, event.y)
        text=f'实体显示平面 X={x:.1f}  Y={y:.1f}；二维图不推测地面高度或可插眼性。'
        if self.grid_layer.get() and self.raw_grid is not None:
            from .map_root_assets import sample_grid
            cell=sample_grid(self.raw_grid,x,y)
            text+=(f" GNV候选格({cell['column']},{cell['row']})=0x{cell['raw_value']:02X}（含义未核验）" if cell else ' GNV候选框外：未知。')
        self.cursor.set(text)

    def redraw(self):
        from PIL import ImageTk
        width, height = max(64, self.world_canvas.winfo_width()), max(64, self.world_canvas.winfo_height())
        self.view = replace(self.view, width=width, height=height)
        frame = self.scene['frames'][self.frame_names().index(self.frame_index.get())]
        mode = 'historical_tower_model' if self.mode.get().startswith('旧') else 'asset_window'
        self.world_photo = ImageTk.PhotoImage(world_image(self.scene, self.texture, self.view,
            selected=self.selected, physics=self.physics.get(), labels=self.labels.get(), wards=self.wards.get(),
            selected_ward=self.selected_ward,grid_texture=self.grid_texture,grid_layer=self.grid_layer.get(),
            tree_layer=self.tree_layer.get(),ward_phase=self.ward_phase.get()), master=self.window)
        self.world_canvas.delete('all')
        self.world_canvas.create_image(0, 0, anchor='nw', image=self.world_photo)
        side = max(64, min(self.shot_canvas.winfo_width(), self.shot_canvas.winfo_height()))
        self.shot_photo = ImageTk.PhotoImage(screenshot_image(self.scene, self.package, frame, mode, self.selected, side), master=self.window)
        self.shot_canvas.delete('all')
        self.shot_canvas.create_image(0, 0, anchor='nw', image=self.shot_photo)
        layer=self.scene.get('ward_candidates')
        if layer:
            context=ward_status(layer,self.view.map_model)
            count=len(visible_points(layer,self.view.map_model,self.ward_phase.get(),allow_unverified=True))
            label={'unknown':'录像地图版本未知，仅作预览','legacy_unbound':'旧资料未绑定版本，仅作预览',
                'mismatch':'录像地图不匹配，已阻止绘制','same_map_bytes_other_context':'地图包相同、版本上下文不同，待核验',
                'exact_identity':'地图身份吻合，坐标投影仍待核验'}[context['source_map_relation']]
            self.ward_count.set(f"候选 {count}/{len(layer['points'])}条 · {label}")
        if self.selected_ward is not None:
            layer = self.scene['ward_candidates']
            p = next(p for p in layer['points'] if p['id'] == self.selected_ward)
            x,y = ward_candidate_xy(layer, p,self.view.map_model)
            self.detail.set(f"历史{'假眼' if p['ward_type']=='observer' else '真眼'} {p['id']}，放置比赛时间={p['placed_game_time_s']}秒；原始XY={p['raw_world_xy']}\n"
                f"候选平移后XY=({x:.3f}, {y:.3f})；录像地图版本、落点及与当前模型对齐均未验证；不叠入右侧不同场景。")
            return
        point = next(p for p in self.scene['markers'] if p['id'] == self.selected)
        pixel = project_marker(self.scene, point, frame, mode)
        xyz = ', '.join(f'{v:.3f}' for v in point['world_xyz'])
        self.detail.set(f"选中：{NAMES.get(point['classname'], point['classname'])} / 原始XYZ=({xyz})\n"
            f"截图预测=({pixel[0]:.2f}, {pixel[1]:.2f})，原图{frame['size'][0]}×{frame['size'][1]}；实体原点未认证为图标中心。")


def show(package):
    import tkinter as tk
    window = tk.Tk()
    PrototypeWindow(window, package)
    window.mainloop()
