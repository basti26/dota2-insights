"""Offline pixel annotations with frozen roles; never display fitted predictions."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path

from .native_camera_calibration import _anchor_arrays
from .native_camera_image import open_image_reference, _read
from .native_camera_review import validate_orientation, validate_position


def read_json(path):
    path=Path(path)
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('annotation_regular_file_required')
    with path.open('rb') as stream:raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('annotation_input_limit')
    return raw,json.loads(raw)


class AnchorDraft:
    def __init__(self, path):
        self.path=Path(path).resolve();self.raw,self.plan=read_json(path)
        p=self.plan
        required={'schema_version','kind','native_viewport','image_reference','anchors'}
        camera_keys={'native_orientation','native_position'}
        if (not isinstance(p,dict) or set(p)-required-camera_keys-{'region_check'} or not required<=set(p)
                or len(set(p) & camera_keys) != 1
                or type(p['schema_version']) is not int or p['schema_version']!=1
                or p['kind']!='native_anchor_pixel_plan'):
            raise ValueError('annotation_plan_version')
        self.camera_key = 'native_orientation' if 'native_orientation' in p else 'native_position'
        if self.camera_key == 'native_orientation':
            validate_orientation(p[self.camera_key])
        else:
            validate_position(p[self.camera_key])
        rows=p['anchors'];seen=set();worlds=set()
        if not isinstance(rows,list) or not 10<=len(rows)<=32:raise ValueError('annotation_anchor_count')
        fields={'id','role','coordinate_basis','source_sha256','world_xyz'}
        for row in rows:
            if (not isinstance(row,dict) or not fields<=set(row) or set(row)-fields-{'point_semantics'}
                    or not isinstance(row['id'],str) or not 1<=len(row['id'])<=100 or row['id'] in seen
                    or any(ord(c)<32 for c in row['id']) or row['role'] not in {'fit','check'}
                    or row['coordinate_basis']!='centered_world_xyz'
                    or not isinstance(row['source_sha256'],str) or len(row['source_sha256'])!=64
                    or any(c not in '0123456789abcdef' for c in row['source_sha256'])):
                raise ValueError('annotation_anchor_identity_or_provenance')
            if self.camera_key == 'native_position' or 'point_semantics' in row:
                semantics = row.get('point_semantics')
                if (not isinstance(semantics, str) or not semantics.strip() or len(semantics)>200
                        or any(ord(c)<32 for c in semantics)):
                    raise ValueError('annotation_point_semantics_required')
            xyz=row['world_xyz']
            if (not isinstance(xyz,list) or len(xyz)!=3 or any(type(v) not in (int,float)
                    or not math.isfinite(v) or abs(v)>1e7 for v in xyz) or tuple(xyz) in worlds):
                raise ValueError('annotation_world_coordinate')
            seen.add(row['id']);worlds.add(tuple(xyz))
        if sum(r['role']=='fit' for r in rows)<6 or sum(r['role']=='check' for r in rows)<4:
            raise ValueError('annotation_separate_roles_required')
        self.plan_sha=hashlib.sha256(self.raw).hexdigest()
        self.points={r['id']:{'state':'pending','screen_xy':None} for r in rows}
        from PIL import Image
        with self.source():
            source=self.path.parent.joinpath(*Path(p['image_reference']['file']).parts)
            pixels=_read(source)
            if hashlib.sha256(pixels).hexdigest()!=p['image_reference']['sha256']:
                raise ValueError('annotation_image_changed')
            with Image.open(BytesIO(pixels)) as image:self.image=image.convert('RGB')

    def source(self):
        if read_json(self.path)[0]!=self.raw:raise ValueError('annotation_plan_changed')
        if self.plan!=json.loads(self.raw):raise ValueError('annotation_plan_modified_in_memory')
        return open_image_reference(self.path.parent,self.plan['image_reference'],
                                    self.plan['native_viewport'],required=True)

    def mark(self, identity, point=None, *, state='marked'):
        if identity not in self.points or state not in {'marked','pending','unidentifiable'}:
            raise ValueError('annotation_point_state')
        if state=='marked':
            size=self.plan['native_viewport']
            if (not isinstance(point,(list,tuple)) or len(point)!=2 or any(type(v) not in (int,float)
                    or not math.isfinite(v) or not 0<=v<=size[i]-1 for i,v in enumerate(point))):
                raise ValueError('annotation_pixel_outside_image')
            point=list(point)
        else:
            if point is not None:raise ValueError('annotation_unmarked_pixel')
        self.points[identity]={'state':state,'screen_xy':point}

    def draft(self):
        return {'schema_version':1,'kind':'native_anchor_pixel_draft','plan_sha256':self.plan_sha,
            'image_sha256':self.plan['image_reference']['sha256'],'points':deepcopy(self.points),
            'pixel_annotation_truth_verified':False,'live_usable':False}

    def restore(self, value):
        if (not isinstance(value,dict) or type(value.get('schema_version')) is not int
                or value['schema_version']!=1 or value.get('kind')!='native_anchor_pixel_draft'
                or value.get('plan_sha256')!=self.plan_sha
                or value.get('image_sha256')!=self.plan['image_reference']['sha256']
                or value.get('pixel_annotation_truth_verified') is not False or value.get('live_usable') is not False
                or not isinstance(value.get('points'),dict) or set(value['points'])!=set(self.points)):
            raise ValueError('annotation_draft_identity')
        previous=deepcopy(self.points)
        try:
            for identity,point in value['points'].items():
                if not isinstance(point,dict) or set(point)!={'state','screen_xy'}:
                    raise ValueError('annotation_point_state')
                self.mark(identity,point['screen_xy'],state=point['state'])
        except (ValueError,TypeError,KeyError):
            self.points=previous
            raise ValueError('annotation_draft_points') from None

    def calibration_input(self):
        if any(p['state']!='marked' for p in self.points.values()):
            raise ValueError('annotation_incomplete')
        rows=[dict(row,screen_xy=list(self.points[row['id']]['screen_xy'])) for row in self.plan['anchors']]
        _anchor_arrays(rows,self.plan['native_viewport'])
        data={k:deepcopy(self.plan[k]) for k in (self.camera_key,'native_viewport','image_reference')}
        kind = ('explicit_native_orientation_anchor_input' if self.camera_key == 'native_orientation'
                else 'explicit_native_camera_anchor_input')
        data.update(schema_version=1,kind=kind,anchors=rows,
            pixel_annotation_review={'plan_sha256':self.plan_sha,'method':'explicit_pixel_annotations',
                'pixel_annotation_truth_verified':False,'live_usable':False})
        if 'region_check' in self.plan:data['region_check']=deepcopy(self.plan['region_check'])
        return data

    def save(self, output, *, complete=False):
        output=Path(output)
        if output.parent.resolve()!=self.path.parent:
            raise ValueError('annotation_output_must_share_source_directory')
        with self.source():
            value=self.calibration_input() if complete else self.draft()
            raw=json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)
            # Serialize and verify sources before exclusive publication.
            if read_json(self.path)[0]!=self.raw:raise ValueError('annotation_plan_changed')
        with output.open('x',encoding='utf-8') as stream:stream.write(raw)


class AnchorEditor:
    def __init__(self, root, draft):
        import tkinter as tk
        from tkinter import ttk
        self.root,self.draft=root,draft
        self.frame=ttk.Frame(root,padding=8);self.frame.pack(fill='both',expand=True)
        ttk.Label(self.frame,text='标注明确的地标点；无法辨认可留空。拟合／检查角色固定，页面不显示投影预测。').pack(anchor='w')
        bar=ttk.Frame(self.frame);bar.pack(fill='x',pady=6)
        for label,scale in [('适合窗口',None),('原尺寸',1.),('放大2倍',2.)]:
            ttk.Button(bar,text=label,command=lambda s=scale:self.zoom(s)).pack(side='left')
        ttk.Button(bar,text='清除此点',command=lambda:self.clear('pending')).pack(side='left')
        ttk.Button(bar,text='不可辨认',command=lambda:self.clear('unidentifiable')).pack(side='left')
        ttk.Button(bar,text='保存草稿',command=lambda:self.choose_save(False)).pack(side='right')
        ttk.Button(bar,text='导出标定输入',command=lambda:self.choose_save(True)).pack(side='right')
        content=ttk.Frame(self.frame);content.pack(fill='both',expand=True)
        self.list=tk.Listbox(content,width=28,exportselection=False);self.list.pack(side='left',fill='y')
        viewport=ttk.Frame(content);viewport.pack(side='left',fill='both',expand=True)
        self.canvas=tk.Canvas(viewport,width=1000,height=650,bg='#20252a',highlightthickness=0)
        xbar=ttk.Scrollbar(viewport,orient='horizontal',command=self.canvas.xview)
        ybar=ttk.Scrollbar(viewport,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set,yscrollcommand=ybar.set)
        xbar.pack(side='bottom',fill='x');ybar.pack(side='right',fill='y');self.canvas.pack(fill='both',expand=True)
        self.status=ttk.Label(self.frame);self.status.pack(anchor='w',pady=4)
        self.detail=ttk.Label(self.frame,wraplength=1100);self.detail.pack(anchor='w')
        self.canvas.bind('<Button-1>',self.click)
        self.list.bind('<<ListboxSelect>>',lambda _event:self.show_selected())
        self.refresh();self.list.selection_set(0);self.show_selected();self.zoom(None)

    def refresh(self):
        selected=self.list.curselection();self.list.delete(0,'end')
        for row in self.draft.plan['anchors']:
            point=self.draft.points[row['id']]
            state={'pending':'未标','marked':'已标','unidentifiable':'不可辨认'}[point['state']]
            role='拟合' if row['role']=='fit' else '检查'
            self.list.insert('end',f"{row['id']} · {role} · {state}")
        if selected:self.list.selection_set(selected[0])
        count=sum(p['state']=='marked' for p in self.draft.points.values())
        self.status.configure(text=f'已标 {count}/{len(self.draft.points)}；图片绑定不等于标注真值或镜头同帧认证。')
        self.show_selected()

    def show_selected(self):
        selected=self.list.curselection()
        if not selected:
            self.detail.configure(text='请选择一个地标，核对其三维点语义后再标像素。')
            return
        row=self.draft.plan['anchors'][selected[0]]
        xyz=', '.join(f'{v:.2f}' for v in row['world_xyz'])
        point=self.draft.points[row['id']]['screen_xy']
        pixel='未标' if point is None else ', '.join(f'{v:.2f}' for v in point)
        semantics = row.get('point_semantics')
        label = f"地标 {row['id']} · 世界 XYZ ({xyz}) · 原图像素 ({pixel})"
        self.detail.configure(text=f'{label}\n取点位置 {semantics}' if semantics else label)

    def zoom(self, scale):
        from PIL import Image,ImageTk
        width,height=self.draft.image.size
        scale=min(1.,1000/width,650/height) if scale is None else scale
        self.display_size=(max(1,round(width*scale)),max(1,round(height*scale)))
        self.photo=ImageTk.PhotoImage(self.draft.image.resize(self.display_size,Image.Resampling.LANCZOS),master=self.canvas)
        self.canvas.delete('all');self.canvas.create_image(0,0,image=self.photo,anchor='nw')
        self.canvas.configure(scrollregion=(0,0,*self.display_size));self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
        self.draw_points()

    def draw_points(self):
        self.canvas.delete('marker')
        width,height=self.draft.image.size;dw,dh=self.display_size
        for row in self.draft.plan['anchors']:
            point=self.draft.points[row['id']]['screen_xy']
            if point is None:continue
            x,y=(point[0]+.5)*dw/width-.5,(point[1]+.5)*dh/height-.5
            color='#70cfff' if row['role']=='fit' else '#ffc568'
            self.canvas.create_line(x-6,y,x+6,y,fill=color,tags='marker')
            self.canvas.create_line(x,y-6,x,y+6,fill=color,tags='marker')
            self.canvas.create_text(x+9,y+9,text=row['id'],fill=color,anchor='nw',tags='marker')

    def click(self, event):
        selected=self.list.curselection()
        if not selected:return
        x,y=self.canvas.canvasx(event.x),self.canvas.canvasy(event.y);dw,dh=self.display_size
        if not 0<=x<dw or not 0<=y<dh:return
        width,height=self.draft.image.size
        point=[round(max(0,min(width-1,(x+.5)*width/dw-.5)),3),
               round(max(0,min(height-1,(y+.5)*height/dh-.5)),3)]
        self.draft.mark(self.draft.plan['anchors'][selected[0]]['id'],point)
        self.refresh();self.draw_points()

    def clear(self, state):
        selected=self.list.curselection()
        if selected:
            self.draft.mark(self.draft.plan['anchors'][selected[0]]['id'],state=state)
            self.refresh();self.draw_points()

    def choose_save(self, complete):
        from tkinter import filedialog,messagebox
        output=filedialog.asksaveasfilename(parent=self.root,initialdir=self.draft.path.parent,
            initialfile='anchors.json' if complete else 'anchor-draft.json',defaultextension='.json')
        if not output:return
        try:
            self.draft.save(output,complete=complete)
        except (OSError,ValueError):
            messagebox.showerror('无法保存','请确认标注完整、来源未变，并在原输入目录选择一个新文件名。',parent=self.root)
            return
        self.status.configure(text='已保存；仍需独立核对标注和镜头对应关系。')
