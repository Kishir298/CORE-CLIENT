"""DPI-aware geometry, wrapping controls and keyboard-accessible scrolling."""
import os
import tkinter as tk
from tkinter import font, ttk


def configure_dpi():
    """Request Windows system DPI awareness before Tk creates a window."""
    if os.name!='nt':return
    import ctypes
    try:ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))
    except (AttributeError,OSError):
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError):pass


def initial_geometry(screen_width, screen_height):
    return max(160,min(1100,screen_width-48)),max(160,min(780,screen_height-80))


def flow_positions(width, sizes, gap=8):
    row=0;x=0;result=[]
    for size in sizes:
        if x and x+size>width:row+=1;x=0
        result.append((row,x));x+=size+gap
    return result


def wrap_text(text, available, measure):
    lines=[]
    for original in text.splitlines():
        current=''
        for word in original.split():
            if current and measure(current+' '+word)>available:lines.append(current);current=word
            else:current=(current+' '+word).strip()
        lines.append(current)
    return '\n'.join(lines)


class FlowRow(ttk.Frame):
    def __init__(self,parent,**kwargs):
        super().__init__(parent,**kwargs)
        self.pending=None;self.original={}
        self.pack_propagate(False)
        self.bind('<Configure>',self.schedule)
        self.bind('<Destroy>',lambda e:self.after_cancel(self.pending) if e.widget is self and self.pending else None)
    def schedule(self,event=None):
        if self.pending is None:self.pending=self.after_idle(self.reflow)
    def reflow(self):
        self.pending=None
        if not self.winfo_exists():return
        width=max(40,self.winfo_width());f=font.nametofont('TkDefaultFont')
        children=self.winfo_children();changed=False
        for item in children:
            if isinstance(item,ttk.Button):
                text=self.original.setdefault(item,str(item.cget('text')))
                wrapped=wrap_text(text,max(20,width-36),f.measure)
                if item.cget('text')!=wrapped:item.configure(text=wrapped);changed=True
            if isinstance(item,ttk.Combobox):
                initial=self.original.setdefault(item,int(item.cget('width')))
                item.configure(width=max(1,min(initial,int((width-32)/max(1,f.measure('0'))))))
        sizes=[min(width,item.winfo_reqwidth()) for item in children]
        positions=flow_positions(width,sizes)
        heights={}
        for item,(row,_) in zip(children,positions):heights[row]=max(heights.get(row,0),item.winfo_reqheight())
        offsets={};total=0
        for row,height in sorted(heights.items()):offsets[row]=total;total+=height+6
        for item,(row,x),width_needed in zip(children,positions,sizes):
            if item.winfo_manager()=='pack':item.pack_forget()
            item.place(x=x,y=offsets[row],width=width_needed,height=item.winfo_reqheight())
        if self.cget('height')!=total:self.configure(height=total)
        if changed:self.schedule()



class ScrollPage(ttk.Frame):
    def __init__(self,parent,**kwargs):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,highlightthickness=0,width=1,height=1,takefocus=False)
        self.vertical=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vertical.set)
        self.vertical.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.content=ttk.Frame(self.canvas,padding=kwargs.get('padding',12))
        self.window=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        self.content.bind('<Configure>',self.changed);self.canvas.bind('<Configure>',self.resized)
        self.bind('<Destroy>',self.destroyed)
        self.content.bind('<FocusIn>',self.reveal,add='+')
        # Each page gets a unique bind tag, rather than a global mouse-wheel grab.
        self.tag='PortalScroll'+str(id(self))
        self.canvas.bindtags((self.tag,*self.canvas.bindtags()))
        self.bind_class(self.tag,'<MouseWheel>',self.wheel)
        self.bind_class(self.tag,'<Button-4>',lambda e:self.wheel(e,-1))
        self.bind_class(self.tag,'<Button-5>',lambda e:self.wheel(e,1))
    def destroyed(self,event):
        if event.widget is self:
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>','<FocusIn>'):self.unbind_class(self.tag,sequence)
    def wheel(self,event,direction=None):
        direction=direction if direction is not None else (-1 if event.delta>0 else 1)
        target=event.widget
        if isinstance(target,(tk.Text,tk.Listbox)):
            first,last=target.yview()
            if (direction<0 and first>0) or (direction>0 and last<1):
                target.yview_scroll(direction*3,'units');return 'break'
        self.canvas.yview_scroll(direction*3,'units');return 'break'
    def changed(self,event=None):
        height=max(self.canvas.winfo_height(),self.content.winfo_reqheight())
        if int(float(self.canvas.itemcget(self.window,'height') or 0))!=height:
            self.canvas.itemconfigure(self.window,height=height)
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
    def resized(self,event):
        self.canvas.itemconfigure(self.window,width=max(1,event.width))
        self.wrap(self.content,max(40,event.width-32))
        self.changed()
    def wrap(self,parent,width):
        for child in parent.winfo_children():
            if isinstance(child,ScrollPage):continue
            if self.tag not in child.bindtags():
                child.bindtags((child.bindtags()[0],self.tag,*child.bindtags()[1:]))
                child.bind('<FocusIn>',self.reveal,add='+')
            if isinstance(child,ttk.Label):child.configure(wraplength=width)
            if isinstance(child,(ttk.Entry,ttk.Combobox)):
                char=max(1,font.nametofont('TkDefaultFont').measure('0'))
                initial=getattr(child,'_portal_width',int(child.cget('width')))
                child._portal_width=initial
                child.configure(width=max(1,min(initial,int((width-32)/char))))
            if isinstance(child,FlowRow):child.schedule()
            self.wrap(child,width)
    def reveal(self,event):
        self.update_idletasks()
        y=event.widget.winfo_rooty()-self.content.winfo_rooty()
        height=event.widget.winfo_height();top=self.canvas.canvasy(0);viewport=self.canvas.winfo_height()
        total=max(1,self.content.winfo_height())
        if y<top:self.canvas.yview_moveto(max(0,y-8)/total)
        elif y+height>top+viewport:self.canvas.yview_moveto(max(0,y+height-viewport+12)/total)
